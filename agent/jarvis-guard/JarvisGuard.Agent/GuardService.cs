using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Text;
using JarvisGuard.Core;
using Microsoft.Win32;

namespace JarvisGuard.Agent;

/// <summary>
/// The PC's guard loop. Polls the hub once a minute, publishes a public
/// status file the tray and the lock surface read, and enforces the decision:
/// a locked PC signs its interactive sessions out. When the hub cannot be
/// reached (or refuses this PC's key) the cached rules still lock on the local
/// clock: a parent's pause, bedtime, school hours and the day's limit, counted
/// with this PC's own minutes (<see cref="OfflineLimit"/>).
/// </summary>
public sealed class GuardService
{
    [DllImport("wtsapi32.dll", SetLastError = true)]
    private static extern bool WTSEnumerateSessionsW(IntPtr server, uint reserved, uint version, out IntPtr ppSessionInfo, out uint count);

    [DllImport("wtsapi32.dll")]
    private static extern bool WTSFreeMemory(IntPtr memory);

    [DllImport("wtsapi32.dll", SetLastError = true)]
    private static extern bool WTSLogoffSession(IntPtr server, uint sessionId, bool wait);

    [StructLayout(LayoutKind.Sequential)]
    private struct WTS_SESSION_INFOW
    {
        public uint SessionId;
        public IntPtr pWinStationName;
        public int State; // 0 = WTSActive
    }

    [DllImport("wtsapi32.dll", SetLastError = true)]
    private static extern bool WTSQueryUserToken(uint sessionId, out IntPtr token);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool GetTokenInformation(IntPtr token, int infoClass, IntPtr info, int length, out int returned);

    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);

    private const int TokenGroupsClass = 2;

    private static readonly IntPtr WTS_CURRENT_SERVER = IntPtr.Zero;
    private const int PollSeconds = 60;
    /// <summary>How often the lock is enforced (and the state file refreshed) between hub polls.</summary>
    private const int TickSeconds = 15;
    private const int CapabilitySeconds = 600;
    /// <summary>How long the agent gives a client to take its answer.</summary>
    private static readonly TimeSpan ResponseWriteTimeout = TimeSpan.FromSeconds(10);

    /// <summary>Tamper events this agent reports (services/gateway _TAMPER_EVENTS).</summary>
    private const string TrayMissingEvent = "tray_missing";
    private const string PipeTakenEvent = "guard_pipe_taken";

    private readonly GuardConfig _config;
    private readonly ApiClient _api;
    private readonly UsageMeter _meter = new(Config.UsagePath);
    private readonly object _stateLock = new();
    private readonly DateTimeOffset _startedAt = DateTimeOffset.Now;

    private readonly LockEnforcer _enforcer = new();
    private readonly OfflineCodes _offline = LoadOfflineCodes();
    private readonly TrayPresence _trays = new();
    private readonly SemaphoreSlim _reporting = new(1, 1);
    private OfflineLimit.Snapshot? _limitSnapshot = LoadLimitSnapshot();
    /// <summary>The last verdict (the hub's, or the offline one) and the state it was published with.</summary>
    private GuardState? _base;
    private DateTimeOffset _lastPoll = DateTimeOffset.MinValue;
    private DateTimeOffset _lastCapability = DateTimeOffset.MinValue;
    /// <summary>How the last status poll went (<see cref="HubReach"/>), so a change is logged once.</summary>
    private string _hubSource = HubReach.Hub;
    /// <summary>Whether a signed-in child session has no tray (minutes not counted), and whether the hub has been told of the latest change.</summary>
    private bool _trayMissing;
    private bool _trayChangeDue;

    public GuardService(GuardConfig config)
    {
        _config = config;
        _meter.DeviceKey = config.DeviceKey;
        _api = new ApiClient(config);
    }

    public async Task RunAsync()
    {
        // The pipe first, before anything else: it must exist before any
        // signed-in user could create one of the same name, and if one already
        // exists this fails here, loudly, instead of serving next to a fake.
        NamedPipeServerStream firstPipe;
        try { firstPipe = CreatePipe(first: true); }
        catch (Exception ex) when (ex is UnauthorizedAccessException or IOException)
        {
            var why = $"another program already holds \\\\.\\pipe\\{Config.PipeName} ({ex.Message}); refusing to run beside it";
            Log($"pipe: {why}");
            try { await _api.PostTamperAsync(PipeTakenEvent, "the guard could not start its pipe"); }
            catch (Exception report) { Log($"pipe: could not tell the hub: {report.Message}"); }
            throw new InvalidOperationException($"Jarvis Guard cannot start: {why}.", ex);
        }

        ReportSafeBootIfNeeded();
        // The device was registered once, at install time, by the admin who
        // ran install.ps1. We only report capabilities from here on.
        await ReportCapabilitiesAsync();

        // The pipe server lives for the process; the loop drives polling.
        using var pipeCts = new CancellationTokenSource();
        _ = Task.Run(() => PipeServerAsync(firstPipe, pipeCts.Token));

        // Foreground minutes arrive from the tray over the pipe ("usage"):
        // this SYSTEM process runs in session 0 and cannot see the desktop.

        // The hub is asked once a minute; the lock is enforced every tick, so a
        // child who signs back in during a lock is signed out again on time.
        using var tick = new PeriodicTimer(TimeSpan.FromSeconds(TickSeconds));
        do
        {
            if (DateTimeOffset.UtcNow - _lastPoll >= TimeSpan.FromSeconds(PollSeconds))
            {
                _lastPoll = DateTimeOffset.UtcNow;
                // Anything unexpected is logged in full and the loop goes on:
                // one bad answer must not take the guard down.
                try { await PollOnceAsync(); }
                catch (Exception ex) { Log($"poll: {ex}"); }
            }
            try { EnforceTick(); }
            catch (Exception ex) { Log($"enforce: {ex}"); }
            if (_trayChangeDue) await ReportTrayAsync();
        } while (await tick.WaitForNextTickAsync());
    }

    private void ReportSafeBootIfNeeded()
    {
        try
        {
            var option = Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Control\SafeBoot\Option")?.GetValue("Option");
            var env = Environment.GetEnvironmentVariable("SAFEBOOT_OPTION");
            var safe = (option is int i && i != 0) || !string.IsNullOrWhiteSpace(env);
            if (safe)
            {
                _ = _api.PostTamperAsync("boot_without_guard", "Windows was started in safe mode");
                Log("safe mode detected; reported to the hub");
            }
        }
        catch (Exception ex) { Log($"safe mode check failed: {ex.Message}"); }
    }

    /// <summary>
    /// Whether the tray is running, for the hub: unknown (null) until a new
    /// sign-in has had time to start it, then whether any signed-in child
    /// session is missing one.
    /// </summary>
    private bool? TrayRunning()
    {
        if (DateTimeOffset.Now - _startedAt < TrayPresence.StartGrace) return null;
        lock (_stateLock) return !_trayMissing;
    }

    private async Task ReportCapabilitiesAsync()
    {
        try
        {
            var resp = await _api.ReportCapabilitiesAsync(lockCapable: true, trayRunning: TrayRunning(), webFilterMode: "off");
            _lastCapability = DateTimeOffset.UtcNow;
            Log($"capabilities: {(int)resp.StatusCode}");
        }
        catch (Exception ex) { Log($"capabilities failed: {ex.Message}"); }
    }

    private async Task PollOnceAsync()
    {
        HubStatus? hub = null;
        int? statusCode = null;
        try
        {
            var resp = await _api.GetStatusAsync();
            statusCode = (int)resp.StatusCode;
            if (resp.IsSuccessStatusCode)
            {
                var raw = await resp.Content.ReadAsStringAsync();
                try
                {
                    hub = HubStatus.Parse(raw);
                    Config.WriteRawStatus(raw);
                }
                catch (System.Text.Json.JsonException ex)
                {
                    statusCode = 502;
                    Log($"status: the hub's answer is not JSON ({ex.Message})");
                }
            }
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            Log($"status: {ex.Message}");
        }

        var source = hub is not null ? HubReach.Hub : HubReach.Classify(statusCode);
        if (source != _hubSource)
        {
            Log($"status: {(hub is not null ? "the hub answers again" : HubReach.LogReason(statusCode))}");
            _hubSource = source;
        }

        if (hub is not null)
        {
            // The hub answers wipe_requested; this build deliberately does
            // not wipe. It is logged for the parents' MDM log instead.
            if (hub.WipeRequested) Log("wipe requested by the hub; ignored (no wipe in this build)");

            var now = DateTimeOffset.Now;
            var time = TimeBudget.Apply(hub, _meter.PendingMinutes, now);
            KeepLimitSnapshot(time);
            SetBase(new GuardState
            {
                Locked = hub.Locked,
                Reason = hub.Reason,
                Until = hub.Until,
                RemainingMinutes = time.RemainingMinutes,
                UsedMinutes = time.UsedMinutes,
                AllowedMinutes = time.AllowedMinutes,
                BonusMinutes = hub.BonusMinutes,
                EarnedMinutes = hub.EarnedMinutes,
                BankedMinutes = hub.BankedMinutes,
                NextLockAt = hub.Locked ? null : time.NextLockAt,
                NextLockReason = hub.Locked ? null : time.NextLockReason,
                PausedAt = GuardEvaluator.PausedAt(hub.Rules)?.ToString("O"),
                ThemeId = hub.ThemeId ?? "clean-athletic",
                AsOfUtc = DateTime.UtcNow.ToString("O"),
                Source = HubReach.Hub,
                HubUrl = _config.HubUrl.TrimEnd('/'),
                Username = _config.Username,
            });
            if (DateTimeOffset.UtcNow - _lastCapability > TimeSpan.FromSeconds(CapabilitySeconds))
                await ReportCapabilitiesAsync();
            await ReportOfflineCodesAsync();
            if (_trayChangeDue) await ReportTrayAsync();
            // Near the limit, report every poll so the hub locks on time (and
            // the next poll's numbers are the hub's own).
            if (_meter.DueForFlush || (TimeBudget.ReportNow(time) && _meter.HasUnsent)) await FlushUsageAsync();
            return;
        }

        // No answer, a refused key or a hub error: re-evaluate the cached
        // rules against the local clock, and the day's limit against this
        // PC's own count. Last night's bedtime still locks, and so does a
        // day's limit used up offline.
        var nowOffline = DateTimeOffset.Now;
        var cached = Config.ReadState();
        var (cachedRules, grantedUntil) = Config.ReadRawCache();
        var (day, meterMs) = _meter.TodayTotal;
        var limit = OfflineLimit.Remaining(_limitSnapshot, cachedRules, day, nowOffline.DayOfWeek, meterMs);
        var offline = GuardEvaluator.Evaluate(cachedRules, nowOffline, grantedUntil, limit.RemainingMinutes);

        // Bedtime and school still lock on the local clock offline, so their warnings stand.
        string? nextAt = offline.Locked || cached?.NextLockReason is not ("bedtime" or "school") ? null : cached.NextLockAt;
        string? nextReason = nextAt is null ? null : cached!.NextLockReason;
        if (!offline.Locked && limit.RemainingMinutes is { } left)
        {
            var limitAt = nowOffline.AddMinutes(left);
            if (!DateTimeOffset.TryParse(nextAt, out var other) || limitAt < other)
            {
                nextAt = limitAt.ToString("O");
                nextReason = "limit";
            }
        }
        var midnight = new DateTimeOffset(nowOffline.Date.AddDays(1), nowOffline.Offset);
        SetBase(new GuardState
        {
            Locked = offline.Locked,
            Reason = offline.Reason,
            Until = offline.Reason == "limit" ? midnight.ToString("O") : cached?.Until,
            RemainingMinutes = limit.RemainingMinutes ?? 0,
            UsedMinutes = limit.AllowedMinutes is { } allowed ? allowed - (limit.RemainingMinutes ?? 0) : (int)(meterMs / 60000),
            AllowedMinutes = limit.AllowedMinutes,
            BonusMinutes = cached?.BonusMinutes ?? 0,
            EarnedMinutes = cached?.EarnedMinutes ?? 0,
            BankedMinutes = cached?.BankedMinutes ?? 0,
            NextLockAt = nextAt,
            NextLockReason = nextReason,
            PausedAt = GuardEvaluator.PausedAt(cachedRules)?.ToString("O"),
            ThemeId = cached?.ThemeId ?? "clean-athletic",
            AsOfUtc = cached?.AsOfUtc ?? DateTime.UtcNow.ToString("O"),
            Source = source,
            Problem = HubReach.Describe(source, statusCode),
            HubUrl = _config.HubUrl.TrimEnd('/'),
            Username = _config.Username,
        });
        if (_meter.DueForFlush) await FlushUsageAsync();
    }

    /// <summary>Keep what the hub said is left today, against this PC's count now, for the offline limit.</summary>
    private void KeepLimitSnapshot(TimeBudget.Result time)
    {
        var (day, meterMs) = _meter.TodayTotal;
        var snapshot = new OfflineLimit.Snapshot
        {
            Day = day,
            AllowedMinutes = time.AllowedMinutes,
            RemainingMinutes = time.RemainingMinutes,
            MeterMs = meterMs,
        };
        _limitSnapshot = snapshot;
        try
        {
            var tmp = Config.LimitSnapshotPath + ".tmp";
            File.WriteAllText(tmp, OfflineLimit.Serialize(snapshot));
            File.Move(tmp, Config.LimitSnapshotPath, overwrite: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            Log($"limit snapshot not saved (the offline limit holds until a restart): {ex.Message}");
        }
    }

    private static OfflineLimit.Snapshot? LoadLimitSnapshot()
    {
        try { return OfflineLimit.Parse(File.Exists(Config.LimitSnapshotPath) ? File.ReadAllText(Config.LimitSnapshotPath) : null); }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidDataException)
        {
            Log($"limit snapshot unreadable, the cached rules' limit applies offline until the hub answers: {ex.Message}");
            return null;
        }
    }

    private async Task FlushUsageAsync()
    {
        try
        {
            foreach (var payload in _meter.Snapshot())
            {
                var resp = await _api.PostUsageAsync(payload);
                Log($"usage flush: {(int)resp.StatusCode}");
            }
        }
        catch (Exception ex) { Log($"usage flush failed: {ex.Message}"); }
    }

    private void SetBase(GuardState state)
    {
        lock (_stateLock) _base = state;
    }

    /// <summary>
    /// Apply the last verdict: a parent's unlock on this PC lifts it for a
    /// while (unless the parents paused the PC since); otherwise a locked PC
    /// shows the lock surface (the tray raises it) and signs the child's
    /// session out once its grace has run out. Also notices a signed-in child
    /// session whose tray has gone (its minutes are not being counted).
    /// </summary>
    private void EnforceTick()
    {
        GuardState state;
        IReadOnlyList<uint> signOut;
        lock (_stateLock)
        {
            if (_base is null) return;
            var now = DateTimeOffset.Now;
            var children = ChildSessions();
            var missing = _trays.Missing(children, now);
            if (missing.Count > 0 != _trayMissing)
            {
                _trayMissing = missing.Count > 0;
                _trayChangeDue = true;
                Log(_trayMissing
                    ? $"tray not running in session(s) {string.Join(", ", missing)}: minutes there are not counted; telling the hub"
                    : "tray running again in every signed-in session");
            }
            DateTimeOffset? pausedAt = _base.Reason == "paused" && DateTimeOffset.TryParse(_base.PausedAt, out var p) ? p : null;
            var effective = _enforcer.EffectiveLocked(_base.Locked, now, pausedAt);
            signOut = _enforcer.SessionsToSignOut(effective, effective ? children : Array.Empty<uint>(), now);
            var unlockUntil = _enforcer.ParentUnlockActive(now) ? _enforcer.UnlockUntil : null;
            state = Copy(_base);
            state.Locked = effective;
            if (!effective && _base.Locked) state.Reason = null;
            state.SignOutAt = _enforcer.NextSignOutAt(effective)?.ToString("O");
            state.ParentUnlockUntil = unlockUntil?.ToString("O");
            state.UsageUnknown = _trayMissing;
            Config.WriteState(state);
        }
        foreach (var id in signOut)
        {
            Log($"locked ({state.Reason ?? "unknown reason"}): signing out session {id}");
            WTSLogoffSession(WTS_CURRENT_SERVER, id, false);
        }
    }

    /// <summary>Tell the hub the tray went (a tamper report the parents see) or came back (capabilities).</summary>
    private async Task ReportTrayAsync()
    {
        if (!await _reporting.WaitAsync(0)) return;
        try
        {
            bool missing;
            lock (_stateLock) missing = _trayMissing;
            var caps = await _api.ReportCapabilitiesAsync(lockCapable: true, trayRunning: !missing, webFilterMode: "off");
            var ok = caps.IsSuccessStatusCode;
            if (missing)
            {
                var tamper = await _api.PostTamperAsync(TrayMissingEvent, "screen time on this PC is not being counted");
                ok &= tamper.IsSuccessStatusCode;
                if (!tamper.IsSuccessStatusCode) Log($"tray report: {HubReach.LogReason((int)tamper.StatusCode)}; tried again later");
            }
            lock (_stateLock)
            {
                // Done only if nothing changed while it was on its way.
                if (ok && missing == _trayMissing) _trayChangeDue = false;
            }
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            Log($"tray report: no answer from the hub ({ex.Message}); tried again later");
        }
        catch (Exception ex) { Log($"tray report failed: {ex}"); }
        finally { _reporting.Release(); }
    }

    private static GuardState Copy(GuardState s) =>
        System.Text.Json.JsonSerializer.Deserialize<GuardState>(System.Text.Json.JsonSerializer.Serialize(s, JsonOptions.Web), JsonOptions.Web)!;

    /// <summary>
    /// Active interactive sessions a lock applies to: not session 0, and not a
    /// session whose user is a local administrator (a parent signing in to
    /// their own account at bedtime is not the child; the child must be a
    /// standard user, see install.ps1).
    /// </summary>
    private List<uint> ChildSessions()
    {
        var ids = new List<uint>();
        if (!WTSEnumerateSessionsW(WTS_CURRENT_SERVER, 0, 1, out var infoPtr, out var count)) return ids;
        try
        {
            var size = Marshal.SizeOf<WTS_SESSION_INFOW>();
            for (var i = 0u; i < count; i++)
            {
                var entry = Marshal.PtrToStructure<WTS_SESSION_INFOW>(IntPtr.Add(infoPtr, (int)(i * size)));
                if (entry.State != 0) continue;     // WTSActive only
                if (entry.SessionId == 0) continue; // services session
                if (IsAdministratorSession(entry.SessionId)) continue;
                ids.Add(entry.SessionId);
            }
        }
        finally { WTSFreeMemory(infoPtr); }
        return ids;
    }

    /// <summary>
    /// Whether the session's user belongs to BUILTIN\Administrators. With UAC
    /// the session token is the filtered one, where that group is present but
    /// deny-only, so the group list is read directly rather than through
    /// IsInRole (which would say no for every administrator).
    /// </summary>
    private static bool IsAdministratorSession(uint sessionId)
    {
        if (!WTSQueryUserToken(sessionId, out var token)) return false;
        var buffer = IntPtr.Zero;
        try
        {
            GetTokenInformation(token, TokenGroupsClass, IntPtr.Zero, 0, out var needed);
            if (needed <= 0) return false;
            buffer = Marshal.AllocHGlobal(needed);
            if (!GetTokenInformation(token, TokenGroupsClass, buffer, needed, out _)) return false;
            var groupCount = Marshal.ReadInt32(buffer);
            var admins = new System.Security.Principal.SecurityIdentifier(System.Security.Principal.WellKnownSidType.BuiltinAdministratorsSid, null);
            // TOKEN_GROUPS: a DWORD count, then SID_AND_ATTRIBUTES { PSID; DWORD } aligned to the pointer size.
            var entrySize = IntPtr.Size * 2;
            for (var i = 0; i < groupCount; i++)
            {
                var sidPtr = Marshal.ReadIntPtr(buffer, IntPtr.Size + i * entrySize);
                if (new System.Security.Principal.SecurityIdentifier(sidPtr) == admins) return true;
            }
            return false;
        }
        catch { return false; }
        finally
        {
            if (buffer != IntPtr.Zero) Marshal.FreeHGlobal(buffer);
            CloseHandle(token);
        }
    }

    // ---- the pipe -----------------------------------------------------------

    /// <summary>
    /// One instance of the agent's pipe, with its ACL (<see cref="PipeTrust.ServerSecurity"/>).
    /// The first is made with FirstPipeInstance: if any other process already
    /// holds the name, Windows refuses, and the agent stops rather than serve
    /// beside it. Every later instance is made while an earlier one exists,
    /// which nobody but SYSTEM and Administrators may do.
    /// </summary>
    private static NamedPipeServerStream CreatePipe(bool first) =>
        NamedPipeServerStreamAcl.Create(
            Config.PipeName, PipeDirection.InOut, NamedPipeServerStream.MaxAllowedServerInstances, PipeTransmissionMode.Byte,
            PipeOptions.Asynchronous | (first ? PipeOptions.FirstPipeInstance : PipeOptions.None),
            0, 0, PipeTrust.ServerSecurity(), HandleInheritability.None, (PipeAccessRights)0);

    /// <summary>
    /// Serve the tray and the lock surface, one request per connection. A
    /// listening instance always exists: the next one is created as soon as a
    /// client connects to the current one, before that client is served, so
    /// there is never a moment when another program could take the name.
    /// </summary>
    private async Task PipeServerAsync(NamedPipeServerStream listener, CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                await listener.WaitForConnectionAsync(ct);
            }
            catch (OperationCanceledException)
            {
                await listener.DisposeAsync();
                return;
            }
            catch (IOException ex)
            {
                // The client went before it was accepted: that instance is spent.
                Log($"pipe: a connection failed ({ex.Message})");
                var spent = listener;
                listener = await NextPipeAsync(ct);
                await spent.DisposeAsync();
                continue;
            }
            var connected = listener;
            listener = await NextPipeAsync(ct);
            _ = ServeAsync(connected, ct);
        }
        await listener.DisposeAsync();
    }

    /// <summary>The next listening instance. Failing to make one is logged and retried, never skipped.</summary>
    private static async Task<NamedPipeServerStream> NextPipeAsync(CancellationToken ct)
    {
        while (true)
        {
            try { return CreatePipe(first: false); }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                Log($"pipe: could not create the next instance ({ex.Message}); retrying");
                await Task.Delay(TimeSpan.FromSeconds(1), ct);
            }
        }
    }

    private async Task ServeAsync(NamedPipeServerStream server, CancellationToken ct)
    {
        await using (server)
        {
            try
            {
                PipeProtocol.Response response;
                try
                {
                    var line = await PipeProtocol.ReadLineAsync(server, PipeProtocol.MaxRequestBytes, PipeProtocol.RequestReadTimeout, ct);
                    response = await HandlePipeRequestAsync(line, PipeTrust.ClientSessionId(server));
                }
                catch (PipeProtocol.LineTooLongException)
                {
                    Log("pipe: a request was longer than allowed; refused");
                    response = new PipeProtocol.Response { Ok = false, Error = "The request was too long." };
                }
                catch (TimeoutException)
                {
                    Log("pipe: a client connected and sent nothing in time; closed");
                    return;
                }
                using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
                cts.CancelAfter(ResponseWriteTimeout);
                var bytes = Encoding.UTF8.GetBytes(PipeProtocol.EncodeResponse(response) + "\n");
                await server.WriteAsync(bytes, cts.Token);
                await server.FlushAsync(cts.Token);
            }
            catch (OperationCanceledException) when (!ct.IsCancellationRequested)
            {
                Log("pipe: a client did not take its answer in time; closed");
            }
            catch (OperationCanceledException) { /* the agent is stopping */ }
            catch (IOException ex) { Log($"pipe: {ex.Message}"); }
        }
    }

    /// <summary>
    /// The pipe's dispatcher: one case per <see cref="PipeProtocol.Request.Cmd"/>.
    /// <paramref name="clientSession"/> is the Windows session the request came
    /// from (null when Windows would not say).
    /// </summary>
    private async Task<PipeProtocol.Response> HandlePipeRequestAsync(string? line, uint? clientSession)
    {
        var request = line is null ? null : PipeProtocol.DecodeRequest(line);
        if (request is null) return new PipeProtocol.Response { Ok = false, Error = "The helper could not understand the request." };

        switch (request.Cmd)
        {
            case "status":
                return new PipeProtocol.Response { Ok = true, State = Config.ReadState() };

            case "usage":
                // Every report, even an empty one, is the tray's heartbeat.
                if (clientSession is { } session)
                {
                    lock (_stateLock) _trays.Heard(session, DateTimeOffset.Now);
                }
                _meter.Add(request.Apps);
                return new PipeProtocol.Response { Ok = true };

            case "ask-time":
                if (request.Minutes is < 5 or > 240)
                    return new PipeProtocol.Response { Ok = false, Error = "Pick how much more time to ask for." };
                try
                {
                    var note = request.Note?.Trim();
                    if (note is { Length: > 200 }) note = note[..200];
                    var resp = await _api.AskMoreTimeAsync(request.Minutes, note);
                    if (resp.IsSuccessStatusCode)
                    {
                        Log($"asked for {request.Minutes} more minutes");
                        return new PipeProtocol.Response { Ok = true };
                    }
                    var refusal = HubReach.RefusalFor((int)resp.StatusCode) ?? Detail(await resp.Content.ReadAsStringAsync());
                    return new PipeProtocol.Response { Ok = false, Error = string.IsNullOrWhiteSpace(refusal) ? "That did not work. Try again in a minute." : refusal };
                }
                catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
                {
                    return NoConnection();
                }

            case "parent-unlock":
                // To be replaced by an unlock request a parent approves on their phone.
                if (string.IsNullOrWhiteSpace(request.ParentUsername) || request.ParentPassword is null)
                    return new PipeProtocol.Response { Ok = false, Error = "A parent's username and password are needed." };
                try
                {
                    var resp = await _api.ParentUnlockAsync(request.ParentUsername.Trim().ToLowerInvariant(), request.ParentPassword);
                    var body = await resp.Content.ReadAsStringAsync();
                    if (!resp.IsSuccessStatusCode)
                    {
                        var message = resp.StatusCode == System.Net.HttpStatusCode.TooManyRequests
                            ? "Too many tries. Wait ten minutes, then try again."
                            : "That did not work. Check the parent's username and password.";
                        return new PipeProtocol.Response { Ok = false, Error = message };
                    }
                    // The hub only lifts a parent's pause; bedtime and the day's
                    // limit still read as locked there. The unlock is this PC's
                    // to keep for the time the hub granted.
                    var started = DateTimeOffset.Now;
                    var until = started.AddMinutes(10);
                    try
                    {
                        using var doc = System.Text.Json.JsonDocument.Parse(body);
                        if (doc.RootElement.TryGetProperty("until", out var u) && u.ValueKind == System.Text.Json.JsonValueKind.String
                            && DateTimeOffset.TryParse(u.GetString(), out var parsed)) until = parsed;
                        // The parents' offline-code verifiers come with a real
                        // parent unlock, as on the phone: kept for a later unlock
                        // with no connection.
                        if (doc.RootElement.TryGetProperty("offline_codes", out var codes))
                        {
                            lock (_stateLock) { _offline.Remember(codes); SaveOfflineCodes(); }
                        }
                    }
                    catch (System.Text.Json.JsonException ex) { Log($"parent unlock: the hub's answer is not JSON ({ex.Message}); ten minutes"); }
                    lock (_stateLock) _enforcer.ParentUnlocked(until, started);
                    Log($"unlocked by a parent until {until:O}");
                    try { EnforceTick(); } catch (Exception ex) { Log($"enforce: {ex.Message}"); }
                    var minutes = (int)Math.Ceiling(Math.Max(0, (until - DateTimeOffset.Now).TotalMinutes));
                    return new PipeProtocol.Response { Ok = true, UntilMinutes = minutes };
                }
                catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
                {
                    return NoConnection();
                }

            case "offline-unlock":
            {
                OfflineCodes.Result result;
                var now = DateTimeOffset.Now;
                lock (_stateLock)
                {
                    result = _offline.TryUnlock(request.Code, now);
                    SaveOfflineCodes();
                    if (result.Ok) _enforcer.ParentUnlocked(now + OfflineCodes.UnlockFor, now);
                }
                if (!result.Ok)
                {
                    // Counted and queued: the next good poll (once a minute)
                    // sends the count, so a run of wrong codes is one notice.
                    Log($"offline code refused: {result.Error}");
                    return new PipeProtocol.Response { Ok = false, Error = result.Error };
                }
                Log($"unlocked with {result.Parent}'s offline code; the parents are told when the hub answers");
                try { EnforceTick(); } catch (Exception ex) { Log($"enforce: {ex.Message}"); }
                _ = ReportOfflineCodesAsync();
                return new PipeProtocol.Response { Ok = true, UntilMinutes = (int)OfflineCodes.UnlockFor.TotalMinutes };
            }

            case "message":
            {
                var text = (request.Text ?? "").Trim();
                if (text.Length == 0) return new PipeProtocol.Response { Ok = false, Error = "Type a message first." };
                if (text.Length > 500) text = text[..500];
                try
                {
                    var resp = await _api.MessageParentsAsync(text);
                    var body = await resp.Content.ReadAsStringAsync();
                    if (!resp.IsSuccessStatusCode)
                        return new PipeProtocol.Response { Ok = false, Error = HubReach.RefusalFor((int)resp.StatusCode) ?? Detail(body) ?? "The message did not go. Try again in a minute." };
                    Log("a message to the parents was sent from the lock screen");
                    return new PipeProtocol.Response { Ok = true, Info = SentTo(body) };
                }
                catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
                {
                    return NoConnection();
                }
            }

            case "chores":
                try
                {
                    var resp = await _api.GetChoresAsync(DateTime.Now.ToString("yyyy-MM-dd"));
                    var body = await resp.Content.ReadAsStringAsync();
                    if (!resp.IsSuccessStatusCode)
                        return new PipeProtocol.Response { Ok = false, Error = HubReach.RefusalFor((int)resp.StatusCode) ?? Detail(body) ?? "Chores could not be loaded." };
                    var listing = Chores.Parse(body);
                    return listing.Items is null
                        ? new PipeProtocol.Response { Ok = false, Error = listing.Error }
                        : new PipeProtocol.Response { Ok = true, Chores = listing.Items.ToList() };
                }
                catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
                {
                    return NoConnection();
                }

            case "chore-done":
                if (string.IsNullOrWhiteSpace(request.ChoreId))
                    return new PipeProtocol.Response { Ok = false, Error = "Pick a chore first." };
                try
                {
                    var resp = await _api.CompleteChoreAsync(request.ChoreId.Trim());
                    var body = await resp.Content.ReadAsStringAsync();
                    if (!resp.IsSuccessStatusCode)
                        return new PipeProtocol.Response { Ok = false, Error = HubReach.RefusalFor((int)resp.StatusCode) ?? Detail(body) ?? "That did not work. Try again." };
                    // The hub's next status read sees it ticked off and asks a parent.
                    _lastPoll = DateTimeOffset.MinValue;
                    Log($"chore {request.ChoreId} ticked off from the lock screen");
                    return new PipeProtocol.Response { Ok = true, Info = "Ticked off. A parent checks it was done right." };
                }
                catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
                {
                    return NoConnection();
                }

            default:
                return new PipeProtocol.Response { Ok = false, Error = $"Unknown command '{request.Cmd}'." };
        }
    }

    private static PipeProtocol.Response NoConnection() =>
        new() { Ok = false, Error = "No connection to the family hub. Try again when the PC is online." };

    /// <summary>
    /// Offline codes are never silent: each good code (with whose it was) and
    /// the wrong ones (as a count) go to the hub, now if it answers, else at
    /// the next good poll. A report leaves the queue only once the hub said it
    /// kept it (the gateway answers an error when it could not).
    /// </summary>
    private async Task ReportOfflineCodesAsync()
    {
        if (!await _reporting.WaitAsync(0)) return;
        try
        {
            List<(OfflineCodes.Report Report, string Event, string Detail)> due;
            lock (_stateLock) due = _offline.Pending.Select(r => (r, r.Event, r.Detail)).ToList();
            foreach (var (report, evt, detail) in due)
            {
                var resp = await _api.PostTamperAsync(evt, detail);
                if (!resp.IsSuccessStatusCode)
                {
                    Log($"offline code report ({evt}): {HubReach.LogReason((int)resp.StatusCode)}; kept for the next try");
                    return;
                }
                lock (_stateLock) { _offline.Sent(report, detail); SaveOfflineCodes(); }
                Log($"offline code report sent: {evt} {detail}");
            }
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            Log($"offline code report: no answer from the hub ({ex.Message}); kept for the next good poll");
        }
        catch (Exception ex) { Log($"offline code report failed: {ex}"); }
        finally { _reporting.Release(); }
    }

    private void SaveOfflineCodes()
    {
        try
        {
            var tmp = Config.OfflineCodesPath + ".tmp";
            File.WriteAllText(tmp, _offline.Serialize());
            File.Move(tmp, Config.OfflineCodesPath, overwrite: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            Log($"offline codes not saved (the lockout and the reports hold until a restart): {ex.Message}");
        }
    }

    private static OfflineCodes LoadOfflineCodes()
    {
        try
        {
            return OfflineCodes.Parse(File.Exists(Config.OfflineCodesPath) ? File.ReadAllText(Config.OfflineCodesPath) : null);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidDataException)
        {
            Log($"offline codes unreadable ({ex.Message}): no offline unlock on this PC until a parent's online unlock brings the codes back");
            return OfflineCodes.Unreadable();
        }
    }

    /// <summary>The hub's own reason for a refusal ("Type a message first"), when it gave one.</summary>
    private static string? Detail(string body)
    {
        try
        {
            using var doc = System.Text.Json.JsonDocument.Parse(body);
            if (doc.RootElement.TryGetProperty("detail", out var d) && d.ValueKind == System.Text.Json.JsonValueKind.String) return d.GetString();
            if (doc.RootElement.TryGetProperty("message", out var m) && m.ValueKind == System.Text.Json.JsonValueKind.String) return m.GetString();
        }
        catch (System.Text.Json.JsonException) { /* not JSON: no words of its own */ }
        return null;
    }

    private static string SentTo(string body)
    {
        try
        {
            using var doc = System.Text.Json.JsonDocument.Parse(body);
            if (doc.RootElement.TryGetProperty("sent_to", out var s) && s.ValueKind == System.Text.Json.JsonValueKind.Array)
            {
                var names = s.EnumerateArray().Select(x => x.GetString()).Where(x => !string.IsNullOrEmpty(x)).ToList();
                if (names.Count > 0) return $"Sent to {string.Join(" and ", names)}.";
            }
        }
        catch (System.Text.Json.JsonException) { /* not JSON */ }
        return "Sent.";
    }

    /// <summary>The agent's log (guard.log beside the config); the console too when the file cannot be written.</summary>
    public static void Log(string message)
    {
        var line = $"{DateTime.Now:O} {message}";
        try { File.AppendAllText(Config.LogPath, line + Environment.NewLine); }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            Console.Error.WriteLine($"{line} (guard.log not writable: {ex.Message})");
        }
    }
}
