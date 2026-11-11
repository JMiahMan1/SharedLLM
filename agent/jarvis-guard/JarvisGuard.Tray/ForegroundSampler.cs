using System.Runtime.InteropServices;
using System.Text;

namespace JarvisGuard.Tray;

/// <summary>
/// What is in front on this desktop, sampled every two seconds in the
/// signed-in session (the SYSTEM service runs in session 0 and cannot see
/// the desktop). Skips while the session is locked, the display is off, or
/// the user has been idle for ten minutes: only actual use counts. The
/// tray hands the minutes to the service, which keeps the day's total.
/// Only the executable's name is read: no titles, no content.
/// </summary>
internal sealed class ForegroundSampler
{
    public const int SampleMs = 2000;
    private const int IdleLimitMs = 10 * 60 * 1000;

    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool QueryFullProcessImageNameW(IntPtr hProcess, uint flags, StringBuilder name, ref uint size);
    [DllImport("kernel32.dll")] private static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
    [DllImport("user32.dll")] private static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);

    [StructLayout(LayoutKind.Sequential)]
    private struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }

    private const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;

    private readonly Dictionary<string, long> _pending = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>The Windows lock and sign-in screens: time spent on them is not use.</summary>
    private static readonly HashSet<string> LockScreens = new(StringComparer.OrdinalIgnoreCase) { "lockapp", "logonui" };

    /// <summary>One sample. <paramref name="inUse"/> is false while the session is locked or the display is off.</summary>
    public void Tick(bool inUse)
    {
        try
        {
            // Only actual use counts: not a locked PC, not a dark screen, not an idle one.
            if (!inUse) return;
            if (IdleMilliseconds() > IdleLimitMs) return;
            var exe = ForegroundExeName();
            if (exe is null || LockScreens.Contains(exe)) return;
            _pending.TryGetValue(exe, out var ms);
            _pending[exe] = ms + SampleMs;
        }
        catch { /* metering must never take the tray down */ }
    }

    /// <summary>What was sampled since the last call, and start over.</summary>
    public Dictionary<string, long> Drain()
    {
        var copy = new Dictionary<string, long>(_pending, StringComparer.OrdinalIgnoreCase);
        _pending.Clear();
        return copy;
    }

    /// <summary>Put back what could not be handed over, so it goes with the next report.</summary>
    public void Restore(Dictionary<string, long> apps)
    {
        foreach (var (exe, ms) in apps)
        {
            _pending.TryGetValue(exe, out var had);
            _pending[exe] = had + ms;
        }
    }

    private static long IdleMilliseconds()
    {
        var lii = new LASTINPUTINFO { cbSize = (uint)Marshal.SizeOf<LASTINPUTINFO>() };
        if (!GetLastInputInfo(ref lii)) return 0;
        return (uint)Environment.TickCount - lii.dwTime;
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
