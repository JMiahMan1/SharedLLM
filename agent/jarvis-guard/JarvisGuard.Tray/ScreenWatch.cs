using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace JarvisGuard.Tray;

/// <summary>
/// Whether this session is actually being used: not locked (Win+L, the lock
/// screen after sleep) and the display not off. Only real use counts as screen
/// time. Listens for Windows' own session and display notifications on a
/// hidden message window.
/// </summary>
internal sealed class ScreenWatch : NativeWindow, IDisposable
{
    [DllImport("wtsapi32.dll")] private static extern bool WTSRegisterSessionNotification(IntPtr hWnd, int flags);
    [DllImport("wtsapi32.dll")] private static extern bool WTSUnRegisterSessionNotification(IntPtr hWnd);
    [DllImport("user32.dll")] private static extern IntPtr RegisterPowerSettingNotification(IntPtr hRecipient, ref Guid powerSettingGuid, int flags);
    [DllImport("user32.dll")] private static extern bool UnregisterPowerSettingNotification(IntPtr handle);

    private const int NOTIFY_FOR_THIS_SESSION = 0;
    private const int WM_WTSSESSION_CHANGE = 0x02B1;
    private const int WTS_SESSION_LOCK = 0x7;
    private const int WTS_SESSION_UNLOCK = 0x8;
    private const int WM_POWERBROADCAST = 0x0218;
    private const int PBT_POWERSETTINGCHANGE = 0x8013;
    private const int DEVICE_NOTIFY_WINDOW_HANDLE = 0;
    private static Guid GUID_CONSOLE_DISPLAY_STATE = new("6FE69556-704A-47A0-8F24-C28D936FDA47");

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    private struct POWERBROADCAST_SETTING
    {
        public Guid PowerSetting;
        public uint DataLength;
        public byte Data;
    }

    private readonly IntPtr _powerHandle;

    public bool Locked { get; private set; }
    public bool DisplayOff { get; private set; }
    public bool InUse => !Locked && !DisplayOff;

    public ScreenWatch()
    {
        CreateHandle(new CreateParams());
        try { WTSRegisterSessionNotification(Handle, NOTIFY_FOR_THIS_SESSION); } catch { /* falls back to the foreground check */ }
        try { _powerHandle = RegisterPowerSettingNotification(Handle, ref GUID_CONSOLE_DISPLAY_STATE, DEVICE_NOTIFY_WINDOW_HANDLE); } catch { }
    }

    protected override void WndProc(ref Message m)
    {
        if (m.Msg == WM_WTSSESSION_CHANGE)
        {
            var code = m.WParam.ToInt32();
            if (code == WTS_SESSION_LOCK) Locked = true;
            else if (code == WTS_SESSION_UNLOCK) Locked = false;
        }
        else if (m.Msg == WM_POWERBROADCAST && m.WParam.ToInt32() == PBT_POWERSETTINGCHANGE && m.LParam != IntPtr.Zero)
        {
            var setting = Marshal.PtrToStructure<POWERBROADCAST_SETTING>(m.LParam);
            if (setting.PowerSetting == GUID_CONSOLE_DISPLAY_STATE) DisplayOff = setting.Data == 0; // 0 off, 1 on, 2 dimmed
        }
        base.WndProc(ref m);
    }

    public void Dispose()
    {
        try { WTSUnRegisterSessionNotification(Handle); } catch { }
        if (_powerHandle != IntPtr.Zero) try { UnregisterPowerSettingNotification(_powerHandle); } catch { }
        DestroyHandle();
    }
}
