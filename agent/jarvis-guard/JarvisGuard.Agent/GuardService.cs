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

    private static readonly IntPtr WTS_CURRENT_SERVER = IntPtr.Zero;
    private const int PollSeconds = 60;
    private const int CapabilitySeconds = 600;

    private readonly GuardConfig _config;
    private readonly ApiClient _api;
    private readonly UsageMeter _meter = new();
    private readonly object _stateLock = new();

    private bool _wasLocked;
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

        using var meterTimer = new PeriodicTimer(TimeSpan.FromMilliseconds(UsageMeter_SampleMs));
        _ = Task.Run(async () =>
        {
            while (await meterTimer.WaitForNextTickAsync()) _meter.Tick();
        });

        using var pollTimer = new PeriodicTimer(TimeSpan.FromSeconds(PollSeconds));
        // First poll immediately, then on the timer.
        await PollOnceAsync();
        while (await pollTimer.WaitForNextTickAsync()) await PollOnceAsync();
    }

    private const int UsageMeter_SampleMs = 2000;

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

            Publish(new GuardState
            {
                Locked = hub.Locked,
                Reason = hub.Reason,
                Until = hub.Until,
                RemainingMinutes = hub.RemainingMinutes,
                ThemeId = hub.ThemeId ?? "clean-athletic",
                AsOfUtc = DateTime.UtcNow.ToString("O"),
                Source = "hub",
                HubUrl = _config.HubUrl.TrimEnd('/'),
                Username = _config.Username,
            });
            Enforce(hub.Locked, hub.Reason);
            if (DateTimeOffset.UtcNow - _lastCapability > TimeSpan.FromSeconds(CapabilitySeconds))
                await ReportCapabilitiesAsync();
            if (_meter.DueForFlush) await FlushUsageAsync();
            return;
        }

        // Offline: re-evaluate the cached rules against the local clock. Last
        // night's bedtime still locks; absent a cache, fail open.
        _consecutiveFailures++;
        var cached = Config.ReadState();
        var cachedRaw = Config.ReadRawRules();
        var offline = GuardEvaluator.Evaluate(cachedRaw, DateTimeOffset.Now);
        Publish(new GuardState
        {
            Locked = offline.Locked,
            Reason = offline.Reason,
            Until = cached?.Until,
            RemainingMinutes = cached?.RemainingMinutes ?? 0,
            ThemeId = cached?.ThemeId ?? "clean-athletic",
            AsOfUtc = DateTime.UtcNow.ToString("O"),
            Source = "offline",
            HubUrl = _config.HubUrl.TrimEnd('/'),
            Username = _config.Username,
        });
        Enforce(offline.Locked, offline.Reason);
        if (_meter.DueForFlush) await FlushUsageAsync();
    }

    private async Task FlushUsageAsync()
    {
        try
        {
            var payload = _meter.SnapshotAndReset();
            var resp = await _api.PostUsageAsync(payload);
            Log($"usage flush: {(int)resp.StatusCode}");
        }
        catch (Exception ex) { Log($"usage flush failed: {ex.Message}"); }
    }

    private void Publish(GuardState state)
    {
        lock (_stateLock) Config.WriteState(state);
    }

    private void Enforce(bool locked, string? reason)
    {
        if (locked && !_wasLocked)
        {
            Log($"locked ({reason ?? "unknown reason"}): signing out interactive sessions");
            LogoffInteractiveSessions();
        }
        _wasLocked = locked;
    }

    private void LogoffInteractiveSessions()
    {
        if (!WTSEnumerateSessionsW(WTS_CURRENT_SERVER, 0, 1, out var infoPtr, out var count)) return;
        try
        {
            var size = Marshal.SizeOf<WTS_SESSION_INFOW>();
            for (var i = 0u; i < count; i++)
            {
                var entry = Marshal.PtrToStructure<WTS_SESSION_INFOW>(IntPtr.Add(infoPtr, (int)(i * size)));
                if (entry.State != 0) continue;             // WTSActive only
                if (entry.SessionId == 0) continue;          // services session
                var console = WTSGetActiveConsoleSessionId();
                if (entry.SessionId != console && console != uint.MaxValue) continue; // console only
                Log($"signing out session {entry.SessionId}");
                WTSLogoffSession(WTS_CURRENT_SERVER, entry.SessionId, false);
            }
        }
        finally { WTSFreeMemory(infoPtr); }
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
                    _wasLocked = false; // next poll decides afresh
                    return new PipeProtocol.Response { Ok = true, UntilMinutes = 10 };
                }
                catch (Exception ex) { return new PipeProtocol.Response { Ok = false, Error = ex.Message }; }

            default:
                return new PipeProtocol.Response { Ok = false, Error = $"Unknown command '{request.Cmd}'." };
        }
    }

    private static void Log(string message)
    {
        try { File.AppendAllText(Config.LogPath, $"{DateTime.Now:O} {message}{Environment.NewLine}"); }
        catch { /* logging must never take the guard down */ }
    }
}
