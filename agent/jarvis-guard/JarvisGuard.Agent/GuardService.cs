using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Text;
using JarvisGuard.Core;
using Microsoft.Win32;

namespace JarvisGuard.Agent;

/// <summary>
/// The PC's guard loop. Polls the hub once a minute, publishes a public
/// status file the tray and the lock surface read, and enforces the decision:
/// a locked PC signs its interactive sessions out. The fail path is closed
/// for bedtime/school windows (cached rules against the local clock) and
/// open for everything else, matching the phone's offline behaviour.
/// </summary>
public sealed class GuardService
{
    [DllImport("wtsapi32.dll", SetLastError = true)]
    private static extern bool WTSEnumerateSessionsW(IntPtr server, uint reserved, uint version, out IntPtr ppSessionInfo, out uint count);

    [DllImport("wtsapi32.dll")]
    private static extern bool WTSFreeMemory(IntPtr memory);

    [DllImport("wtsapi32.dll", SetLastError = true)]
    private static extern bool WTSLogoffSession(IntPtr server, uint sessionId, bool wait);

    [DllImport("wtsapi32.dll")] private static extern uint WTSGetActiveConsoleSessionId();

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

    private readonly GuardConfig _config;
    private readonly ApiClient _api;
    private readonly UsageMeter _meter = new(Config.UsagePath);
    private readonly object _stateLock = new();

    private readonly LockEnforcer _enforcer = new();
    private readonly OfflineCodes _offline = OfflineCodes.Parse(ReadText(Config.OfflineCodesPath));
    /// <summary>The last verdict (the hub's, or the offline one) and the state it was published with.</summary>
    private GuardState? _base;
    private DateTimeOffset _lastPoll = DateTimeOffset.MinValue;
    private DateTimeOffset _lastCapability = DateTimeOffset.MinValue;
    private int _consecutiveFailures;

    public GuardService(GuardConfig config)
    {
        _config = config;
        _meter.DeviceKey = config.DeviceKey;
        _api = new ApiClient(config);
    }

    public async Task RunAsync()
    {
        ReportSafeBootIfNeeded();
        // The device was registered once, at install time, by the admin who
        // ran install.ps1. We only report capabilities from here on.
        await ReportCapabilitiesAsync();

        // The pipe server lives for the process; the loop drives polling.
        using var pipeCts = new CancellationTokenSource();
        _ = Task.Run(() => PipeServerAsync(pipeCts.Token));

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
                await PollOnceAsync();
            }
            try { EnforceTick(); }
            catch (Exception ex) { Log($"enforce: {ex.Message}"); }
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
        catch { /* detection is best-effort */ }
    }

    private async Task ReportCapabilitiesAsync()
    {
        try
        {
            var resp = await _api.ReportCapabilitiesAsync(lockCapable: true, trayRunning: true, webFilterMode: "off");
            _lastCapability = DateTimeOffset.UtcNow;
            Log($"capabilities: {(int)resp.StatusCode}");
        }
        catch (Exception ex) { Log($"capabilities failed: {ex.Message}"); }
    }

    private async Task PollOnceAsync()
    {
        HubStatus? hub = null;
        try
        {
            var resp = await _api.GetStatusAsync();
            if (resp.IsSuccessStatusCode)
            {
                var raw = await resp.Content.ReadAsStringAsync();
                hub = HubStatus.Parse(raw);
                Config.WriteRawStatus(raw);
                _consecutiveFailures = 0;
            }
            else Log($"status: hub answered {(int)resp.StatusCode}");
        }
        catch (Exception ex) { Log($"status: {ex.Message}"); }

        if (hub is not null)
        {
            // The hub answers wipe_requested; this build deliberately does
            // not wipe. It is logged for the parents' MDM log instead.
            if (hub.WipeRequested) Log("wipe requested by the hub; ignored (no wipe in this build)");

            var time = TimeBudget.Apply(hub, _meter.PendingMinutes, DateTimeOffset.Now);
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
                ThemeId = hub.ThemeId ?? "clean-athletic",
                AsOfUtc = DateTime.UtcNow.ToString("O"),
                Source = "hub",
                HubUrl = _config.HubUrl.TrimEnd('/'),
                Username = _config.Username,
            });
            if (DateTimeOffset.UtcNow - _lastCapability > TimeSpan.FromSeconds(CapabilitySeconds))
                await ReportCapabilitiesAsync();
            await ReportOfflineUnlockIfDueAsync();
            // Near the limit, report every poll so the hub locks on time (and
            // the next poll's numbers are the hub's own).
            if (_meter.DueForFlush || (TimeBudget.ReportNow(time) && _meter.HasUnsent)) await FlushUsageAsync();
            return;
        }

        // Offline: re-evaluate the cached rules against the local clock. Last
        // night's bedtime still locks; absent a cache, fail open.
        _consecutiveFailures++;
        var cached = Config.ReadState();
        var (cachedRules, grantedUntil) = Config.ReadRawCache();
        var offline = GuardEvaluator.Evaluate(cachedRules, DateTimeOffset.Now, grantedUntil);
        SetBase(new GuardState
        {
            Locked = offline.Locked,
            Reason = offline.Reason,
            Until = cached?.Until,
            RemainingMinutes = cached?.RemainingMinutes ?? 0,
            UsedMinutes = cached?.UsedMinutes ?? 0,
            AllowedMinutes = cached?.AllowedMinutes,
            BonusMinutes = cached?.BonusMinutes ?? 0,
            EarnedMinutes = cached?.EarnedMinutes ?? 0,
            BankedMinutes = cached?.BankedMinutes ?? 0,
            // Bedtime and school still lock on the local clock offline, so their warnings stand.
            NextLockAt = offline.Locked || cached?.NextLockReason is not ("bedtime" or "school") ? null : cached.NextLockAt,
            NextLockReason = offline.Locked || cached?.NextLockReason is not ("bedtime" or "school") ? null : cached.NextLockReason,
            ThemeId = cached?.ThemeId ?? "clean-athletic",
            AsOfUtc = DateTime.UtcNow.ToString("O"),
            Source = "offline",
            HubUrl = _config.HubUrl.TrimEnd('/'),
            Username = _config.Username,
        });
        if (_meter.DueForFlush) await FlushUsageAsync();
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
    /// while; otherwise a locked PC shows the lock surface (the tray raises
    /// it) and signs the child's session out once its grace has run out.
    /// </summary>
    private void EnforceTick()
    {
        GuardState state;
        IReadOnlyList<uint> signOut;
        lock (_stateLock)
        {
            if (_base is null) return;
            var now = DateTimeOffset.Now;
            var effective = _enforcer.EffectiveLocked(_base.Locked, now);
            signOut = _enforcer.SessionsToSignOut(effective, effective ? ChildSessions() : Array.Empty<uint>(), now);
            var unlockUntil = _enforcer.ParentUnlockActive(now) ? _enforcer.UnlockUntil : null;
            state = Copy(_base);
            state.Locked = effective;
            if (!effective && _base.Locked) state.Reason = null;
            state.SignOutAt = _enforcer.NextSignOutAt(effective)?.ToString("O");
            state.ParentUnlockUntil = unlockUntil?.ToString("O");
            Config.WriteState(state);
        }
        foreach (var id in signOut)
        {
            Log($"locked ({state.Reason ?? "unknown reason"}): signing out session {id}");
            WTSLogoffSession(WTS_CURRENT_SERVER, id, false);
        }
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

    /// <summary>Serve the tray and the lock surface. One request per connection.</summary>
    private async Task PipeServerAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                var security = new PipeSecurity();
                // The pipe is owned by SYSTEM; the signed-in child (tray, lock
                // surface) must be able to reach it. Authenticated users may
                // read/write the pipe; nothing else is exposed. The pipe carries
                // no secrets - only locked/reason/parent-unlock verdicts.
                security.AddAccessRule(new PipeAccessRule(new System.Security.Principal.SecurityIdentifier(System.Security.Principal.WellKnownSidType.AuthenticatedUserSid, null),
                    PipeAccessRights.ReadWrite, AccessControlType.Allow));
                await using var server = NamedPipeServerStreamAcl.Create(
                    Config.PipeName, PipeDirection.InOut, 4, PipeTransmissionMode.Byte, PipeOptions.None, 0, 0, security,
                    System.IO.HandleInheritability.None, PipeAccessRights.FullControl);
                await server.WaitForConnectionAsync(ct);
                using var reader = new StreamReader(server, Encoding.UTF8, leaveOpen: true);
                var line = await reader.ReadLineAsync(ct);
                var response = await HandlePipeRequestAsync(line);
                var bytes = Encoding.UTF8.GetBytes(PipeProtocol.EncodeResponse(response) + "\n");
                await server.WriteAsync(bytes, ct);
                await server.FlushAsync(ct);
            }
            catch (OperationCanceledException) { break; }
            catch (Exception ex) { Log($"pipe: {ex.Message}"); }
        }
    }

    private async Task<PipeProtocol.Response> HandlePipeRequestAsync(string? line)
    {
        var request = line is null ? null : PipeProtocol.DecodeRequest(line);
        if (request is null) return new PipeProtocol.Response { Ok = false, Error = "The helper could not understand the request." };

        switch (request.Cmd)
        {
            case "status":
                return new PipeProtocol.Response { Ok = true, State = Config.ReadState() };

            case "usage":
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
                    var detail = "";
                    try
                    {
                        using var doc = System.Text.Json.JsonDocument.Parse(await resp.Content.ReadAsStringAsync());
                        if (doc.RootElement.TryGetProperty("detail", out var d) && d.ValueKind == System.Text.Json.JsonValueKind.String)
                            detail = d.GetString() ?? "";
                    }
                    catch { /* no detail to show */ }
                    return new PipeProtocol.Response { Ok = false, Error = string.IsNullOrWhiteSpace(detail) ? "That did not work. Try again in a minute." : detail };
                }
                catch
                {
                    return new PipeProtocol.Response { Ok = false, Error = "No connection to the family hub. Try again when the PC is online." };
                }

            case "parent-unlock":
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
                    var until = DateTimeOffset.Now.AddMinutes(10);
                    try
                    {
                        using var doc = System.Text.Json.JsonDocument.Parse(body);
                        if (doc.RootElement.TryGetProperty("until", out var u) && u.ValueKind == System.Text.Json.JsonValueKind.String
                            && DateTimeOffset.TryParse(u.GetString(), out var parsed)) until = parsed;
                    }
                    catch { /* ten minutes, the hub's own figure */ }
                    // The parents' offline-code verifiers come with a real
                    // parent unlock, as on the phone: kept for a later unlock
                    // with no connection.
                    try
                    {
                        using var doc = System.Text.Json.JsonDocument.Parse(body);
                        if (doc.RootElement.TryGetProperty("offline_codes", out var codes))
                        {
                            lock (_stateLock) { _offline.Remember(codes); SaveOfflineCodes(); }
                        }
                    }
                    catch { /* the codes already kept stay */ }
                    lock (_stateLock) _enforcer.ParentUnlocked(until);
                    Log($"unlocked by a parent until {until:O}");
                    try { EnforceTick(); } catch { /* the next tick publishes it */ }
                    var minutes = (int)Math.Ceiling(Math.Max(0, (until - DateTimeOffset.Now).TotalMinutes));
                    return new PipeProtocol.Response { Ok = true, UntilMinutes = minutes };
                }
                catch (Exception ex) { return new PipeProtocol.Response { Ok = false, Error = ex.Message }; }

            case "offline-unlock":
            {
                OfflineCodes.Result result;
                var now = DateTimeOffset.Now;
                lock (_stateLock)
                {
                    result = _offline.TryUnlock(request.Code, now);
                    SaveOfflineCodes();
                    if (result.Ok) _enforcer.ParentUnlocked(now + OfflineCodes.UnlockFor);
                }
                if (!result.Ok) return new PipeProtocol.Response { Ok = false, Error = result.Error };
                Log("unlocked with a parent's offline code; the parents are told when the hub answers");
                try { EnforceTick(); } catch { /* the next tick publishes it */ }
                _ = ReportOfflineUnlockIfDueAsync();
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
                        return new PipeProtocol.Response { Ok = false, Error = Detail(body) ?? "The message did not go. Try again in a minute." };
                    Log("a message to the parents was sent from the lock screen");
                    return new PipeProtocol.Response { Ok = true, Info = SentTo(body) };
                }
                catch
                {
                    return new PipeProtocol.Response { Ok = false, Error = "No connection to the family hub. Try again when the PC is online." };
                }
            }

            case "chores":
                try
                {
                    var resp = await _api.GetChoresAsync(DateTime.Now.ToString("yyyy-MM-dd"));
                    var body = await resp.Content.ReadAsStringAsync();
                    if (!resp.IsSuccessStatusCode)
                        return new PipeProtocol.Response { Ok = false, Error = Detail(body) ?? "Chores could not be loaded." };
                    var listing = Chores.Parse(body);
                    return listing.Items is null
                        ? new PipeProtocol.Response { Ok = false, Error = listing.Error }
                        : new PipeProtocol.Response { Ok = true, Chores = listing.Items.ToList() };
                }
                catch
                {
                    return new PipeProtocol.Response { Ok = false, Error = "No connection to the family hub. Try again when the PC is online." };
                }

            case "chore-done":
                if (string.IsNullOrWhiteSpace(request.ChoreId))
                    return new PipeProtocol.Response { Ok = false, Error = "Pick a chore first." };
                try
                {
                    var resp = await _api.CompleteChoreAsync(request.ChoreId.Trim());
                    var body = await resp.Content.ReadAsStringAsync();
                    if (!resp.IsSuccessStatusCode)
                        return new PipeProtocol.Response { Ok = false, Error = Detail(body) ?? "That did not work. Try again." };
                    // The hub's next status read sees it ticked off and asks a parent.
                    _lastPoll = DateTimeOffset.MinValue;
                    Log($"chore {request.ChoreId} ticked off from the lock screen");
                    return new PipeProtocol.Response { Ok = true, Info = "Ticked off. A parent checks it was done right." };
                }
                catch
                {
                    return new PipeProtocol.Response { Ok = false, Error = "No connection to the family hub. Try again when the PC is online." };
                }

            default:
                return new PipeProtocol.Response { Ok = false, Error = $"Unknown command '{request.Cmd}'." };
        }
    }

    /// <summary>An offline unlock is never silent: the parents are told as soon as the hub answers.</summary>
    private async Task ReportOfflineUnlockIfDueAsync()
    {
        lock (_stateLock) { if (!_offline.ReportDue) return; }
        try
        {
            var resp = await _api.PostTamperAsync("offline_unlock", "");
            if (resp.IsSuccessStatusCode)
            {
                lock (_stateLock) { _offline.ReportDue = false; SaveOfflineCodes(); }
                Log("offline unlock reported to the hub");
            }
        }
        catch { /* still offline: the next good poll tries again */ }
    }

    private void SaveOfflineCodes()
    {
        try
        {
            var tmp = Config.OfflineCodesPath + ".tmp";
            File.WriteAllText(tmp, _offline.Serialize());
            File.Move(tmp, Config.OfflineCodesPath, overwrite: true);
        }
        catch (Exception ex) { Log($"offline codes not saved: {ex.Message}"); }
    }

    private static string? ReadText(string path)
    {
        try { return File.Exists(path) ? File.ReadAllText(path) : null; }
        catch { return null; }
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
        catch { /* not JSON */ }
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
        catch { /* not JSON */ }
        return "Sent.";
    }

    private static void Log(string message)
    {
        try { File.AppendAllText(Config.LogPath, $"{DateTime.Now:O} {message}{Environment.NewLine}"); }
        catch { /* logging must never take the guard down */ }
    }
}
