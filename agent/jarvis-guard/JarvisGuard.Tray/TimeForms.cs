using System.Drawing;
using System.Windows.Forms;
using JarvisGuard.Core;

namespace JarvisGuard.Tray;

/// <summary>Colours shared by the tray's small windows (the lock surface's slate and amber).</summary>
internal static class Look
{
    public static readonly Color Back = Color.FromArgb(15, 23, 42);
    public static readonly Color Card = Color.FromArgb(30, 41, 59);
    public static readonly Color Line = Color.FromArgb(71, 85, 105);
    public static readonly Color Text = Color.FromArgb(226, 232, 240);
    public static readonly Color Muted = Color.FromArgb(148, 163, 184);
    public static readonly Color Accent = Color.FromArgb(217, 119, 6);
    public static readonly Color OnAccent = Color.FromArgb(15, 23, 42);
    public static readonly Color Error = Color.FromArgb(250, 165, 60);

    public static Label Label(string text, float size, Color color, FontStyle style = FontStyle.Regular) => new()
    {
        Text = text,
        Font = new Font("Segoe UI", size, style),
        ForeColor = color,
        AutoSize = true,
        MaximumSize = new Size(360, 0),
        Margin = new Padding(0, 0, 0, 6),
    };

    public static Button Button(string text, bool primary)
    {
        var b = new Button
        {
            Text = text,
            FlatStyle = FlatStyle.Flat,
            BackColor = primary ? Accent : Card,
            ForeColor = primary ? OnAccent : Text,
            Font = new Font("Segoe UI", 10.5f, primary ? FontStyle.Bold : FontStyle.Regular),
            Height = 40,
            Width = 360,
            Cursor = Cursors.Hand,
            Margin = new Padding(0, 8, 0, 0),
        };
        b.FlatAppearance.BorderColor = primary ? Accent : Line;
        b.FlatAppearance.BorderSize = 1;
        return b;
    }
}

/// <summary>
/// Today's screen time at a glance - left, used of how much, what locks next -
/// with "Ask for more time". A click on the tray shield opens it; with a
/// warning title it is the ten- and five-minute warning, raised over
/// whatever is open.
/// </summary>
internal sealed class TimePanelForm : Form
{
    public TimePanelForm(GuardState? state, string? warningTitle = null, string? warningText = null)
    {
        var warning = !string.IsNullOrEmpty(warningTitle);
        Text = warning ? warningTitle : "Screen time today";
        FormBorderStyle = FormBorderStyle.FixedToolWindow;
        StartPosition = FormStartPosition.Manual;
        TopMost = true;
        ShowInTaskbar = warning;
        BackColor = Look.Back;
        ForeColor = Look.Text;
        AutoSize = true;
        AutoSizeMode = AutoSizeMode.GrowAndShrink;
        MaximizeBox = false;
        MinimizeBox = false;
        KeyPreview = true;
        KeyDown += (_, e) => { if (e.KeyCode == Keys.Escape) Close(); };

        var flow = new FlowLayoutPanel
        {
            FlowDirection = FlowDirection.TopDown,
            WrapContents = false,
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            Padding = new Padding(20, 16, 20, 16),
            BackColor = Look.Back,
        };

        flow.Controls.Add(Look.Label(warning ? warningTitle! : "Screen time today", 15, Look.Text, FontStyle.Bold));
        if (warning && !string.IsNullOrEmpty(warningText)) flow.Controls.Add(Look.Label(warningText, 11.5f, Look.Text));

        if (state is null)
        {
            flow.Controls.Add(Look.Label("Screen time isn't available yet. The PC hasn't heard from the family hub.", 10.5f, Look.Muted));
        }
        else
        {
            flow.Controls.Add(Look.Label(TimeText.Headline(state), 18, Look.Text, FontStyle.Bold));
            flow.Controls.Add(Look.Label(TimeText.UsedLine(state), 11, Look.Muted));
            if (TimeText.Limited(state))
            {
                var track = new Panel { Width = 360, Height = 10, BackColor = Look.Card, Margin = new Padding(0, 4, 0, 8) };
                var fill = new Panel { Height = 10, Width = 360 * TimeText.UsedPercent(state) / 100, BackColor = Look.Accent };
                track.Controls.Add(fill);
                flow.Controls.Add(track);
            }
            foreach (var line in new[] { TimeText.ExtraLine(state), TimeText.NextLine(state) })
                if (!string.IsNullOrEmpty(line)) flow.Controls.Add(Look.Label(line, 10.5f, Look.Muted));
            if (state.Source == "offline")
                flow.Controls.Add(Look.Label("Offline: these are the last numbers the hub sent.", 9.5f, Look.Muted));
        }

        var canAsk = state is not null && (TimeText.Limited(state) || state.Locked || state.NextLockAt is not null);
        if (canAsk)
        {
            var ask = Look.Button("Ask for more time…", primary: true);
            ask.Click += (_, _) =>
            {
                using var form = new AskTimeForm();
                form.ShowDialog(this);
                if (form.Sent) Close();
            };
            flow.Controls.Add(ask);
            AcceptButton = ask;
        }
        var close = Look.Button(warning ? "OK" : "Close", primary: false);
        close.Click += (_, _) => Close();
        flow.Controls.Add(close);
        CancelButton = close;

        Controls.Add(flow);

        Load += (_, _) =>
        {
            // Bottom-right, above the taskbar, where the tray shield is.
            var area = Screen.PrimaryScreen?.WorkingArea ?? new Rectangle(0, 0, 1280, 720);
            Location = new Point(area.Right - Width - 16, area.Bottom - Height - 16);
        };
        Shown += (_, _) => Activate();
    }
}

/// <summary>
/// "Ask for more time": how much and why, sent to the parents as a real
/// request they answer from their phones (Grant / Not today). Goes through
/// the guard service, which holds this PC's key.
/// </summary>
internal sealed class AskTimeForm : Form
{
    private readonly RadioButton[] _choices;
    private readonly TextBox _note = new();
    private readonly Label _status = Look.Label("", 10, Look.Muted);
    private readonly Button _send = Look.Button("Send request", primary: true);
    private static readonly int[] Minutes = { 15, 30, 60 };

    public bool Sent { get; private set; }

    public AskTimeForm()
    {
        Text = "Ask for more time";
        FormBorderStyle = FormBorderStyle.FixedDialog;
        StartPosition = FormStartPosition.CenterScreen;
        TopMost = true;
        BackColor = Look.Back;
        ForeColor = Look.Text;
        AutoSize = true;
        AutoSizeMode = AutoSizeMode.GrowAndShrink;
        MaximizeBox = false;
        MinimizeBox = false;

        var flow = new FlowLayoutPanel
        {
            FlowDirection = FlowDirection.TopDown,
            WrapContents = false,
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            Padding = new Padding(20, 16, 20, 16),
            BackColor = Look.Back,
        };
        flow.Controls.Add(Look.Label("Ask for more time", 15, Look.Text, FontStyle.Bold));
        flow.Controls.Add(Look.Label("Your parents get a notification and can allow it for today.", 10.5f, Look.Muted));

        var row = new FlowLayoutPanel { AutoSize = true, FlowDirection = FlowDirection.LeftToRight, Margin = new Padding(0, 6, 0, 6), BackColor = Look.Back };
        _choices = Minutes.Select((m, i) => new RadioButton
        {
            Text = m == 60 ? "1 hour" : $"{m} min",
            Appearance = Appearance.Button,
            FlatStyle = FlatStyle.Flat,
            TextAlign = ContentAlignment.MiddleCenter,
            Width = 112,
            Height = 36,
            Checked = m == 30,
            BackColor = Look.Card,
            ForeColor = Look.Text,
            Font = new Font("Segoe UI", 10.5f),
            Margin = new Padding(0, 0, i < 2 ? 12 : 0, 0),
            Cursor = Cursors.Hand,
        }).ToArray();
        foreach (var c in _choices)
        {
            c.FlatAppearance.BorderColor = Look.Line;
            c.FlatAppearance.CheckedBackColor = Look.Accent;
            c.CheckedChanged += (_, _) => c.ForeColor = c.Checked ? Look.OnAccent : Look.Text;
            c.ForeColor = c.Checked ? Look.OnAccent : Look.Text;
            row.Controls.Add(c);
        }
        flow.Controls.Add(row);

        _note.PlaceholderText = "Why? (optional)";
        _note.Multiline = true;
        _note.Width = 360;
        _note.Height = 56;
        _note.MaxLength = 200;
        _note.BackColor = Look.Card;
        _note.ForeColor = Look.Text;
        _note.BorderStyle = BorderStyle.FixedSingle;
        _note.Font = new Font("Segoe UI", 10.5f);
        flow.Controls.Add(_note);
        flow.Controls.Add(_status);

        _send.Click += async (_, _) => await SendAsync();
        flow.Controls.Add(_send);
        var cancel = Look.Button("Cancel", primary: false);
        cancel.Click += (_, _) => Close();
        flow.Controls.Add(cancel);
        CancelButton = cancel;

        Controls.Add(flow);
    }

    private async Task SendAsync()
    {
        var minutes = Minutes[Array.FindIndex(_choices, c => c.Checked) is var i && i >= 0 ? i : 1];
        _send.Enabled = false;
        _send.Text = "Sending…";
        _status.ForeColor = Look.Muted;
        _status.Text = "Asking…";
        var response = await PipeProtocol.SendAsync(Config.PipeName, new PipeProtocol.Request
        {
            Cmd = "ask-time",
            Minutes = minutes,
            Note = _note.Text,
        }, timeoutMs: 15000);
        _send.Enabled = true;
        _send.Text = "Send request";
        if (response?.Ok == true)
        {
            Sent = true;
            MessageBox.Show(this, "Asked! A parent will answer soon.", "Ask for more time", MessageBoxButtons.OK, MessageBoxIcon.Information);
            Close();
            return;
        }
        _status.ForeColor = Look.Error;
        _status.Text = response?.Error ?? "No answer: the Jarvis Guard service is not running.";
    }
}
