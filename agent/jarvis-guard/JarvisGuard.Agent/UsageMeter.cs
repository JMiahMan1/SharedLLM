using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using JarvisGuard.Core;

namespace JarvisGuard.Agent;

/// <summary>
/// Foreground-app meter: what the PC is doing, accumulated per executable and
/// posted to the hub as the same shape of usage report the phone posts.
/// Samples every two seconds, skips when the user has been idle for ten.
/// </summary>
public sealed class UsageMeter
{
    private const int SampleMs = 2000;
    private const int IdleLimitMs = 10 * 60 * 1000;
    private const int FlushEverySeconds = 1800;

    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool QueryFullProcessImageNameW(IntPtr hProcess, uint flags, StringBuilder name, ref uint size);
    [DllImport("kernel32.dll")] private static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
    [DllImport("user32.dll")] private static extern bool GetLastInputInfo(out LASTINPUTINFO plii);
    [DllImport("user32.dll")] private static extern uint GetTickCount();

    [StructLayout(LayoutKind.Sequential)]
    private struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }

    private const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;

    private readonly Dictionary<string, long> _appsMs = new(StringComparer.OrdinalIgnoreCase);
    private readonly object _lock = new();
    private long _totalMs;
    private string? _firstUse, _lastUse;
    private long _lastFlush = Stopwatch.GetTimestamp();

    /// <summary>One sample. Cheap enough to call every two seconds.</summary>
    public void Tick()
    {
        try
        {
            if (IdleMilliseconds() > IdleLimitMs) return;
            var exe = ForegroundExeName();
            if (exe is null) return;
            lock (_lock)
            {
                _appsMs.TryGetValue(exe, out var ms);
                _appsMs[exe] = ms + SampleMs;
                _totalMs += SampleMs;
                var now = DateTime.UtcNow.ToString("O");
                _firstUse ??= now;
                _lastUse = now;
            }
        }
        catch { /* metering must never take the agent down */ }
    }

    public bool DueForFlush => Stopwatch.GetElapsedTime(_lastFlush) > TimeSpan.FromSeconds(FlushEverySeconds);

    /// <summary>Build the payload and reset the counters.</summary>
    public object SnapshotAndReset()
    {
        lock (_lock)
        {
            var apps = _appsMs
                .OrderByDescending(kv => kv.Value)
                .Take(50)
                .Select(kv => new { package = kv.Key, millis = kv.Value })
                .ToArray();
            var local = DateTime.Now;
            var payload = new
            {
                day = local.Date.ToString("yyyy-MM-dd"),
                millis = _totalMs,
                first_use = _firstUse,
                last_use = _lastUse,
                sessions = 1,
                apps,
                source = "windows",
                device_key = DeviceKey,
            };
            _appsMs.Clear();
            _totalMs = 0;
            _firstUse = null;
            _lastUse = null;
            _lastFlush = Stopwatch.GetTimestamp();
            return payload;
        }
    }

    public string? DeviceKey { get; set; }

    private static uint IdleMilliseconds()
    {
        var lii = new LASTINPUTINFO { cbSize = (uint)Marshal.SizeOf<LASTINPUTINFO>() };
        if (!GetLastInputInfo(out lii)) return 0;
        return GetTickCount() - lii.dwTime;
    }

    private static string? ForegroundExeName()
    {
        var hWnd = GetForegroundWindow();
        if (hWnd == IntPtr.Zero) return null;
        GetWindowThreadProcessId(hWnd, out uint pid);
        if (pid == 0) return null;
        var h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid);
        if (h == IntPtr.Zero) return null;
        try
        {
            var sb = new StringBuilder(1024);
            uint size = 1024;
            if (!QueryFullProcessImageNameW(h, 0, sb, ref size)) return null;
            return Path.GetFileNameWithoutExtension(sb.ToString()).ToLowerInvariant();
        }
        finally { CloseHandle(h); }
    }
}
