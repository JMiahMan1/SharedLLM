using System.Drawing;
using System.Windows.Forms;
using JarvisGuard.Core;

namespace JarvisGuard.Overlay;

/// <summary>
/// The lock surface a locked child sees: why the PC is locked, until when, and
/// the countdown to the sign-out, with the same ways out as the phone's lock
/// screen, all on this screen: ask for more time, message a parent, see and
/// tick off today's chores, and a parent's sign-in (or a parent's offline code
/// when the hub cannot be reached).
///
/// It does not keep anyone out by itself (the agent signs the session out);
/// it makes sure the child is never locked by a machine that will not say why.
/// Closing it takes a parent's unlock or the hub lifting the lock.
///
/// Layout: one centred column that wraps and scrolls, with sizes scaled from
/// the screen's DPI, so it reads the same at 1366x768 and 4K, and at 100% or
/// 150% scaling. Every button is at least 56 px (logical) tall for touch.
/// </summary>
public sealed class LockForm : Form
{
    private static readonly Color Back = Color.FromArgb(15, 23, 42);
    private static readonly Color Card = Color.FromArgb(30, 41, 59);
    private static readonly Color Line = Color.FromArgb(71, 85, 105);
    private static readonly Color Ink = Color.FromArgb(226, 232, 240);
    private static readonly Color Muted = Color.FromArgb(148, 163, 184);
    private static readonly Color Accent = Color.FromArgb(217, 119, 6);
    private static readonly Color OnAccent = Color.FromArgb(15, 23, 42);
    private static readonly Color Warn = Color.FromArgb(250, 165, 60);
    private static readonly Color Good = Color.FromArgb(110, 231, 183);
    private static readonly int[] AskMinutes = { 15, 30, 60 };

    private readonly float _scale;
    private readonly Panel _scroller = new() { Dock = DockStyle.Fill, AutoScroll = true };
    private readonly FlowLayoutPanel _column = new()
    {
        FlowDirection = FlowDirection.TopDown, WrapContents = false, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
    };
    private readonly Label _title;
    private readonly Label _until;
    private readonly Label _countdown;
    private readonly FlowLayoutPanel _actions;
    private readonly FlowLayoutPanel _sheet;
    private readonly Label _status;
    private readonly System.Windows.Forms.Timer _refresh = new() { Interval = 1000 };
    private bool _allowingClose;
    private bool _busy;
    private int _columnWidth;
    private string? _lastReason;

    public LockForm()
    {
        AutoScaleMode = AutoScaleMode.None; // sizes are scaled by hand from the DPI below
        _scale = Math.Max(1f, DeviceDpi / 96f);
        FormBorderStyle = FormBorderStyle.None;
        WindowState = FormWindowState.Maximized;
        TopMost = true;
        BackColor = Back;
        ForeColor = Ink;
        ShowInTaskbar = true;
        KeyPreview = true;
        Text = "Jarvis Guard";

        _title = MakeLabel("This PC is locked", 30, FontStyle.Bold, Ink, bottom: 6);
        _until = MakeLabel("", 15, FontStyle.Regular, Muted, bottom: 4);
        _countdown = MakeLabel("", 13, FontStyle.Regular, Warn, bottom: 20);
        var managed = MakeLabel("This PC is managed by the family hub.", 11, FontStyle.Regular, Muted, bottom: 20);

        _actions = new FlowLayoutPanel { AutoSize = true, WrapContents = true, Margin = Pad(0, 0, 0, 8) };
        _actions.Controls.Add(MakeButton("Ask for more time", primary: true, (_, _) => ShowAsk()));
        _actions.Controls.Add(MakeButton("Message a parent", primary: false, (_, _) => ShowMessage()));
        _actions.Controls.Add(MakeButton("Chores", primary: false, (_, _) => _ = ShowChoresAsync()));
        _actions.Controls.Add(MakeButton("A parent can unlock", primary: false, (_, _) => ShowUnlock(offline: false)));

        _sheet = new FlowLayoutPanel
        {
            FlowDirection = FlowDirection.TopDown, WrapContents = false, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
            BackColor = Card, Padding = Pad(20, 16, 20, 16), Margin = Pad(0, 8, 0, 12), Visible = false,
        };
        _status = MakeLabel("", 13, FontStyle.Regular, Warn, bottom: 12);
        _status.Visible = false;

        _column.Controls.AddRange(new Control[] { _title, _until, _countdown, _actions, _sheet, _status, managed });
        _scroller.Controls.Add(_column);
        Controls.Add(_scroller);

        _scroller.Resize += (_, _) => Relayout();
        _column.SizeChanged += (_, _) => Center();
        _refresh.Tick += (_, _) => RefreshState();
        _refresh.Start();
        Shown += (_, _) => { Relayout(); RefreshState(); };

        FormClosing += (_, args) =>
        {
            if (args.CloseReason == CloseReason.UserClosing && !_allowingClose) args.Cancel = true;
        };
        KeyDown += (_, args) => { if (args.KeyCode == Keys.Escape) args.Handled = true; };
    }

    // ---- layout ---------------------------------------------------------

    private int S(int logical) => (int)Math.Round(logical * _scale);
    private Padding Pad(int l, int t, int r, int b) => new(S(l), S(t), S(r), S(b));

    /// <summary>The column is at most 760 logical px wide and never wider than the screen, less a margin.</summary>
    private void Relayout()
    {
        var available = Math.Max(S(280), _scroller.ClientSize.Width - S(48));
        _columnWidth = Math.Min(available, S(760));
        foreach (Control c in _column.Controls) Fit(c, _columnWidth);
        Center();
    }

    private void Fit(Control c, int width)
    {
        switch (c)
        {
            case Label l:
                l.MaximumSize = new Size(width, 0);
                break;
            case FlowLayoutPanel f when f == _sheet:
                f.MaximumSize = new Size(width, 0);
                f.MinimumSize = new Size(width, 0);
                var inner = width - f.Padding.Horizontal;
                foreach (Control child in f.Controls) Fit(child, inner);
                break;
            case FlowLayoutPanel f:
                f.MaximumSize = new Size(width, 0);
                // A chore row: its title wraps beside the Done button.
                foreach (Control child in f.Controls) if (child is Label l) l.MaximumSize = new Size(Math.Max(S(120), width - S(150)), 0);
                break;
            case TextBox t:
                t.Width = width;
                break;
        }
    }

    private void Center()
    {
        var x = Math.Max(S(24), (_scroller.ClientSize.Width - _column.Width) / 2);
        var y = Math.Max(S(24), (_scroller.ClientSize.Height - _column.Height) / 3);
        _column.Location = new Point(x + _scroller.AutoScrollPosition.X, y + _scroller.AutoScrollPosition.Y);
    }

    private Label MakeLabel(string text, float points, FontStyle style, Color color, int bottom) => new()
    {
        Text = text, AutoSize = true, ForeColor = color, Font = new Font("Segoe UI", points, style), Margin = Pad(0, 0, 0, bottom),
    };

    private Button MakeButton(string text, bool primary, EventHandler onClick)
    {
        var b = new Button
        {
            Text = text, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
            MinimumSize = new Size(S(56), S(56)), Padding = Pad(18, 8, 18, 8), Margin = Pad(0, 0, 12, 12),
            FlatStyle = FlatStyle.Flat, Font = new Font("Segoe UI", 13, primary ? FontStyle.Bold : FontStyle.Regular),
            BackColor = primary ? Accent : Card, ForeColor = primary ? OnAccent : Ink, Cursor = Cursors.Hand, TabStop = true,
        };
        b.FlatAppearance.BorderColor = primary ? Accent : Line;
        b.FlatAppearance.BorderSize = 1;
        b.Click += onClick;
        return b;
    }

    private TextBox MakeBox(string placeholder, bool secret = false, bool multiline = false)
    {
        var t = new TextBox
        {
            PlaceholderText = placeholder, Font = new Font("Segoe UI", 14), BackColor = Back, ForeColor = Ink,
            BorderStyle = BorderStyle.FixedSingle, Margin = Pad(0, 0, 0, 12), Multiline = multiline,
        };
        if (secret) t.UseSystemPasswordChar = true;
        if (multiline)
        {
            t.Height = S(110);
            t.ScrollBars = ScrollBars.Vertical;
            t.MaxLength = 500;
        }
        return t;
    }

    /// <summary>Open one sheet under the buttons, replacing whatever was there.</summary>
    private void OpenSheet(string heading, params Control[] controls)
    {
        _sheet.SuspendLayout();
        var old = _sheet.Controls.Cast<Control>().ToList();
        _sheet.Controls.Clear();
        foreach (var c in old) c.Dispose();
        _sheet.Controls.Add(MakeLabel(heading, 15, FontStyle.Bold, Ink, bottom: 12));
        _sheet.Controls.AddRange(controls);
        _sheet.Visible = true;
        _sheet.ResumeLayout();
        Say(null);
        Relayout();
        controls.OfType<TextBox>().FirstOrDefault()?.Focus();
        _scroller.ScrollControlIntoView(_sheet);
    }

    private void Say(string? message, bool good = false)
    {
        _status.Text = message ?? "";
        _status.ForeColor = good ? Good : Warn;
        _status.Visible = !string.IsNullOrEmpty(message);
    }

    // ---- the sheets -----------------------------------------------------

    private void ShowAsk()
    {
        var chosen = 30;
        var choices = new FlowLayoutPanel { AutoSize = true, WrapContents = true, Margin = Pad(0, 0, 0, 4) };
        foreach (var m in AskMinutes)
        {
            var minutes = m;
            var b = MakeButton($"{m} minutes", primary: m == chosen, (sender, _) =>
            {
                chosen = minutes;
                foreach (Button other in choices.Controls)
                {
                    var on = other == sender;
                    other.BackColor = on ? Accent : Card;
                    other.ForeColor = on ? OnAccent : Ink;
                    other.FlatAppearance.BorderColor = on ? Accent : Line;
                }
            });
            choices.Controls.Add(b);
        }
        var note = MakeBox("Why? (optional)");
        note.MaxLength = 200;
        var send = MakeButton("Send to a parent", primary: true, async (_, _) =>
        {
            var r = await Send(new PipeProtocol.Request { Cmd = "ask-time", Minutes = chosen, Note = note.Text });
            if (r?.Ok == true) Say($"Asked for {chosen} more minutes. A parent answers from their phone.", good: true);
        });
        OpenSheet("How much more time?", choices, note, send);
    }

    private void ShowMessage()
    {
        var text = MakeBox("Write to your parents", multiline: true);
        var send = MakeButton("Send", primary: true, async (_, _) =>
        {
            var r = await Send(new PipeProtocol.Request { Cmd = "message", Text = text.Text }, timeoutMs: 20000);
            if (r?.Ok == true)
            {
                text.Text = "";
                Say(r.Info ?? "Sent.", good: true);
            }
        });
        OpenSheet("Message a parent", text, send);
    }

    private async Task ShowChoresAsync()
    {
        OpenSheet("Today's chores", MakeLabel("Loading your chores...", 13, FontStyle.Regular, Muted, bottom: 4));
        var r = await Send(new PipeProtocol.Request { Cmd = "chores" }, timeoutMs: 40000);
        if (r?.Ok != true) return;
        var rows = new List<Control>();
        if (r.Chores is not { Count: > 0 })
        {
            rows.Add(MakeLabel("No chores on the list today.", 13, FontStyle.Regular, Muted, bottom: 4));
        }
        else
        {
            foreach (var chore in r.Chores)
            {
                var row = new FlowLayoutPanel { AutoSize = true, WrapContents = true, Margin = Pad(0, 0, 0, 8) };
                var title = MakeLabel(chore.Title, 14, FontStyle.Regular, chore.Completed ? Muted : Ink, bottom: 0);
                title.Margin = Pad(0, 14, 16, 0);
                row.Controls.Add(title);
                if (chore.Completed)
                {
                    var done = MakeLabel("Ticked off", 12, FontStyle.Italic, Good, bottom: 0);
                    done.Margin = Pad(0, 16, 0, 0);
                    row.Controls.Add(done);
                }
                else
                {
                    var id = chore.Id;
                    row.Controls.Add(MakeButton("Done", primary: true, async (_, _) =>
                    {
                        var d = await Send(new PipeProtocol.Request { Cmd = "chore-done", ChoreId = id }, timeoutMs: 40000);
                        if (d?.Ok == true)
                        {
                            await ShowChoresAsync();
                            Say(d.Info ?? "Ticked off.", good: true);
                        }
                    }));
                }
                rows.Add(row);
            }
            rows.Add(MakeLabel("A parent checks each chore you tick off before it counts.", 11, FontStyle.Regular, Muted, bottom: 0));
        }
        OpenSheet("Today's chores", rows.ToArray());
    }

    private void ShowUnlock(bool offline)
    {
        if (offline)
        {
            var code = MakeBox("A parent's offline code", secret: true);
            var go = MakeButton("Unlock", primary: true, async (_, _) =>
            {
                var r = await Send(new PipeProtocol.Request { Cmd = "offline-unlock", Code = code.Text });
                code.Text = "";
                if (r?.Ok == true) CloseUnlocked();
            });
            var back = MakeButton("Use a parent's sign-in instead", primary: false, (_, _) => ShowUnlock(offline: false));
            var note = MakeLabel("For when the PC has no connection. The parents are told once it is back online.",
                11, FontStyle.Regular, Muted, bottom: 8);
            OpenSheet("A parent's offline code", code, go, back, note);
            return;
        }
        var user = MakeBox("Parent's username");
        var password = MakeBox("Parent's password", secret: true);
        var unlock = MakeButton("Unlock", primary: true, async (_, _) =>
        {
            if (string.IsNullOrWhiteSpace(user.Text) || password.Text.Length == 0)
            {
                Say("Enter the parent's username and password.");
                return;
            }
            var r = await Send(new PipeProtocol.Request
            {
                Cmd = "parent-unlock", ParentUsername = user.Text.Trim(), ParentPassword = password.Text,
            }, timeoutMs: 20000);
            password.Text = "";
            if (r?.Ok == true) CloseUnlocked();
        });
        var offlineLink = MakeButton("No connection? Use an offline code", primary: false, (_, _) => ShowUnlock(offline: true));
        var told = MakeLabel("The unlock lasts ten minutes and the other parents are told.", 11, FontStyle.Regular, Muted, bottom: 8);
        OpenSheet("A parent signs in", user, password, unlock, offlineLink, told);
    }

    // ---- talking to the guard -------------------------------------------

    /// <summary>One request to the guard, with its error shown; null or not ok means it did not work.</summary>
    private async Task<PipeProtocol.Response?> Send(PipeProtocol.Request request, int timeoutMs = 15000)
    {
        if (_busy) return null;
        _busy = true;
        UseWaitCursor = true;
        try
        {
            var r = await PipeProtocol.SendAsync(Config.PipeName, request, timeoutMs);
            if (r is null) Say("No answer from the Jarvis Guard service. Try again in a moment.");
            else if (!r.Ok) Say(r.Error ?? "That did not work. Try again.");
            return r;
        }
        finally
        {
            _busy = false;
            UseWaitCursor = false;
        }
    }

    private void CloseUnlocked()
    {
        _allowingClose = true;
        Close();
    }

    /// <summary>The agent's public state file, read every second for the countdown.</summary>
    private void RefreshState()
    {
        var s = Config.ReadState();
        if (s is null)
        {
            _title.Text = "This PC is locked";
            _until.Text = "The Jarvis Guard service is not answering.";
            _countdown.Text = "";
            return;
        }
        if (!s.Locked)
        {
            CloseUnlocked();
            return;
        }
        if (s.Reason != _lastReason)
        {
            _lastReason = s.Reason;
            _title.Text = LockText.Title(s.Reason);
            // A "chores first" lock opens straight onto today's chores.
            if (s.Reason == "chores" && !_sheet.Visible) _ = ShowChoresAsync();
        }
        _until.Text = LockText.Until(s.Reason, s.Until);
        _countdown.Text = LockText.Countdown(s.SignOutAt, DateTimeOffset.Now);
    }
}
