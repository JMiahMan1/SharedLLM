using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Windows.Forms;
using JarvisGuard.Core;

// The signage every user sees: this PC is managed by the family hub.
namespace JarvisGuard.Tray;

internal static class Program
{
    public static void Run()
    {
        ApplicationConfiguration.Initialize();
        Application.Run(new TrayContext());
    }
}

internal sealed class TrayContext : ApplicationContext
{
    private readonly NotifyIcon _icon;
    private readonly ContextMenuStrip _menu;
    private readonly System.Windows.Forms.Timer _refresh = new() { Interval = 5000 };
    private bool _locked;

    public TrayContext()
    {
        var hubItem = new ToolStripMenuItem("Open the family hub");
        hubItem.Click += (_, _) => OpenUrl(state: null);
        var askItem = new ToolStripMenuItem("Ask a parent for more time…");
        askItem.Click += (_, _) => OpenMemberPage();
        var unlockItem = new ToolStripMenuItem("A parent can unlock…");
        unlockItem.Click += (_, _) => LaunchOverlay();
        var aboutItem = new ToolStripMenuItem("About Jarvis Guard");
        aboutItem.Click += (_, _) => ShowAbout();

        _menu = new ContextMenuStrip();
        _menu.Items.AddRange(new ToolStripItem[] { hubItem, askItem, unlockItem, new ToolStripSeparator(), aboutItem });

        _icon = new NotifyIcon
        {
            Icon = MakeIcon(locked: false),
            Text = "This PC is managed by the family hub",
            ContextMenuStrip = _menu,
            Visible = true,
        };
        _icon.DoubleClick += (_, _) => OpenUrl(null);

        _refresh.Tick += (_, _) => Refresh();
        _refresh.Start();
        Refresh();
    }

    private void Refresh()
    {
        var state = Config.ReadState();
        var locked = state?.Locked == true;
        if (locked != _locked)
        {
            _icon.Icon = MakeIcon(locked);
            _locked = locked;
        }
        _icon.Text = locked
            ? $"Jarvis guard: locked - {ReasonText(state?.Reason)}{(state?.Until is null ? "" : $" until {state.Until}")}"
            : "This PC is managed by the family hub";
    }

    private static string ReasonText(string? reason) => reason switch
    {
        "bedtime" => "bedtime",
        "school" => "school hours",
        "paused" => "paused by a parent",
        "chores" => "chores first",
        "limit" => "enough screen time for today",
        _ => "locked",
    };

    private static Icon MakeIcon(bool locked)
    {
        // Drawn in place: a slate shield normally, amber when the PC is locked.
        using var bmp = new Bitmap(32, 32);
        using var g = Graphics.FromImage(bmp);
        g.SmoothingMode = SmoothingMode.AntiAlias;
        var back = locked ? Color.FromArgb(217, 119, 6) : Color.FromArgb(51, 65, 85);
        using (var brush = new SolidBrush(back))
        {
            g.FillRectangle(brush, 0, 0, 32, 32);
        }
        var shield = new GraphicsPath();
        shield.AddPolygon(new[]
        {
            new PointF(16, 3),
            new PointF(27, 8),
            new PointF(27, 15),
            new PointF(16, 29),
            new PointF(5, 15),
            new PointF(5, 8),
        });
        using (var brush = new SolidBrush(Color.FromArgb(240, 245, 250))) g.FillPath(brush, shield);
        using (var brush = new SolidBrush(Color.FromArgb(15, 23, 42)))
        {
            var font = new Font("Segoe UI", locked ? 12 : 11, FontStyle.Bold);
            var size = g.MeasureString(locked ? "!" : "J", font);
            g.DrawString(locked ? "!" : "J", font, brush, (32 - size.Width) / 2f, (32 - size.Height) / 2f - 1);
        }
        return Icon.FromHandle(bmp.GetHicon());
    }

    private static void OpenUrl(object? state)
    {
        var hub = Config.ReadState()?.HubUrl;
        if (string.IsNullOrWhiteSpace(hub)) { MessageBox.Show("The family hub address is not configured yet."); return; }
        try { Process.Start(new ProcessStartInfo(hub) { UseShellExecute = true }); }
        catch { MessageBox.Show("Could not open the family hub."); }
    }

    private static void OpenMemberPage()
    {
        var state = Config.ReadState();
        var hub = state?.HubUrl;
        var user = state?.Username;
        if (string.IsNullOrWhiteSpace(hub) || string.IsNullOrWhiteSpace(user)) { MessageBox.Show("The family hub address is not configured yet."); return; }
        try { Process.Start(new ProcessStartInfo($"{hub}/family/member/{Uri.EscapeDataString(user)}") { UseShellExecute = true }); }
        catch { MessageBox.Show("Could not open the family hub."); }
    }

    private static void LaunchOverlay()
    {
        var overlay = Path.Combine(AppContext.BaseDirectory, "jarvis-guard.exe");
        try { Process.Start(new ProcessStartInfo(overlay, "--overlay") { UseShellExecute = true }); }
        catch { MessageBox.Show("The lock screen helper could not be started."); }
    }

    private static void ShowAbout()
    {
        var state = Config.ReadState();
        MessageBox.Show(
            $"Jarvis Guard\nThis PC is managed by the family hub.\n\nHub: {state?.HubUrl ?? "not configured"}\nSigned in as: {state?.Username ?? "unknown"}\nVersion: {typeof(Program).Assembly.GetName().Version}",
            "About Jarvis Guard", MessageBoxButtons.OK, MessageBoxIcon.Information);
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _refresh.Dispose();
            _icon.Visible = false;
            _icon.Dispose();
        }
        base.Dispose(disposing);
    }
}
