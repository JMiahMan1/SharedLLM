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
    private readonly System.Windows.Forms.Timer _sample = new() { Interval = ForegroundSampler.SampleMs };
    private readonly System.Windows.Forms.Timer _report = new() { Interval = 30_000 };
    private readonly ForegroundSampler _sampler = new();
    private readonly ScreenWatch _screen = new();
    private readonly LockWarning.Tracker _warnings = new();
    private TimePanelForm? _panel;
    private bool _locked;
    private bool _reporting;

    public TrayContext()
    {
        var timeItem = new ToolStripMenuItem("Screen time today…") { Font = new Font("Segoe UI", 9, FontStyle.Bold) };
        timeItem.Click += (_, _) => ShowPanel(null, null);
        var hubItem = new ToolStripMenuItem("Open the family hub");
        hubItem.Click += (_, _) => OpenUrl(state: null);
        var askItem = new ToolStripMenuItem("Ask a parent for more time…");
        askItem.Click += (_, _) => AskForTime();
        var unlockItem = new ToolStripMenuItem("A parent can unlock…");
        unlockItem.Click += (_, _) => LaunchOverlay();
        var aboutItem = new ToolStripMenuItem("About Jarvis Guard");
        aboutItem.Click += (_, _) => ShowAbout();

        _menu = new ContextMenuStrip();
        _menu.Items.AddRange(new ToolStripItem[] { timeItem, askItem, hubItem, unlockItem, new ToolStripSeparator(), aboutItem });

        _icon = new NotifyIcon
        {
            Icon = MakeIcon(locked: false),
            Text = "This PC is managed by the family hub",
            ContextMenuStrip = _menu,
            Visible = true,
        };
        // One click shows today's time: used, left, and a way to ask for more.
        _icon.MouseClick += (_, e) => { if (e.Button == MouseButtons.Left) ShowPanel(null, null); };

        _refresh.Tick += (_, _) => Refresh();
        _refresh.Start();
        // Usage is measured here, in the signed-in session (the service cannot
        // see the desktop), and only while the screen is really in use.
        _sample.Tick += (_, _) => _sampler.Tick(_screen.InUse);
        _sample.Start();
        _report.Tick += async (_, _) => await ReportUsageAsync();
        _report.Start();
        Refresh();
    }

    private async Task ReportUsageAsync()
    {
        if (_reporting) return;
        var apps = _sampler.Drain();
        if (apps.Count == 0) return;
        _reporting = true;
        try
        {
            var r = await PipeProtocol.SendAsync(Config.PipeName, new PipeProtocol.Request { Cmd = "usage", Apps = apps });
            if (r?.Ok != true) _sampler.Restore(apps);
        }
        finally { _reporting = false; }
    }

    /// <summary>The time panel (or, with a title, the 10/5-minute warning), one at a time.</summary>
    private void ShowPanel(string? warningTitle, string? warningText)
    {
        if (_panel is { IsDisposed: false }) _panel.Close();
        _panel = new TimePanelForm(Config.ReadState(), warningTitle, warningText);
        _panel.FormClosed += (_, _) => _panel = null;
        _panel.Show();
    }

    private static void AskForTime()
    {
        using var form = new AskTimeForm();
        form.ShowDialog();
    }

    /// <summary>Ten and five minutes before a lock: a dialog with "Ask for more time". One minute before: a heads-up.</summary>
    private void CheckWarning(GuardState? state)
    {
        if (state is null || state.Locked) { _warnings.Next(DateTimeOffset.Now, null, null); return; }
        var at = DateTimeOffset.TryParse(state.NextLockAt, out var parsed) ? parsed : (DateTimeOffset?)null;
        var w = _warnings.Next(DateTimeOffset.Now, at, state.NextLockReason);
        if (w is null) return;
        if (w.Dialog) ShowPanel(w.Title, w.Text);
        else _icon.ShowBalloonTip(10_000, w.Title, w.Text, ToolTipIcon.Warning);
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
        _icon.Text = TimeText.Tooltip(state);
        CheckWarning(state);
    }

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
            _sample.Dispose();
            _report.Dispose();
            _screen.Dispose();
            _icon.Visible = false;
            _icon.Dispose();
        }
        base.Dispose(disposing);
    }
}
