using System.Diagnostics;
using System.Drawing;
using System.Windows.Forms;
using JarvisGuard.Core;

namespace JarvisGuard.Overlay;

/// <summary>
/// The signage a locked child sees. It does not keep anyone out by itself -
/// the agent signs the session out. It exists so the child is never locked by
/// a machine that will not say why, and so a parent can sign in where it
/// happened. Closing it takes a parent's unlock or a hub state change.
/// </summary>
public sealed class LockForm : Form
{
    private readonly Label _title = new();
    private readonly Label _reason = new();
    private readonly Label _until = new();
    private readonly Label _foot = new();
    private readonly Label _error = new();
    private readonly Panel _actionRow = new();
    private readonly Panel _unlockPanel = new();
    private readonly TextBox _user = new();
    private readonly TextBox _password = new();
    private readonly Button _unlockButton = new();
    private readonly Button _toggleUnlockButton = new();
    private readonly System.Windows.Forms.Timer _refresh = new() { Interval = 5000 };

    public LockForm()
    {
        FormBorderStyle = FormBorderStyle.None;
        WindowState = FormWindowState.Maximized;
        TopMost = true;
        BackColor = Color.FromArgb(15, 23, 42);
        ForeColor = Color.FromArgb(226, 232, 240);
        ShowInTaskbar = true;
        KeyPreview = true;

        var layout = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            ColumnCount = 1,
            RowCount = 7,
            Padding = new Padding(64, 64, 64, 40),
        };
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 30));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 70));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));

        _title.Text = "Time's up - this PC is managed by the family hub";
        _title.Font = new Font("Segoe UI", 40, FontStyle.Bold);
        _title.AutoSize = true;
        _title.Margin = new Padding(0, 0, 0, 16);

        _reason.Text = "Locked";
        _reason.Font = new Font("Segoe UI", 22);
        _reason.AutoSize = true;

        _until.Text = "";
        _until.Font = new Font("Segoe UI", 13);
        _until.ForeColor = Color.FromArgb(148, 163, 184);
        _until.AutoSize = true;

        var hubNote = new Label
        {
            Text = "Calls, texts and the Jarvis app still work. A parent can lift the lock, or approve more time, from anywhere.",
            Font = new Font("Segoe UI", 13),
            ForeColor = Color.FromArgb(148, 163, 184),
            AutoSize = true,
        };

        _error.Text = "";
        _error.ForeColor = Color.FromArgb(250, 165, 60);
        _error.AutoSize = true;
        _error.Visible = false;

        _actionRow.Height = 64;
        _actionRow.AutoSize = true;

        var askButton = MakeButton("Ask for more time…");
        askButton.Click += (_, _) =>
        {
            using var ask = new Tray.AskTimeForm();
            ask.ShowDialog(this);
        };
        var messageButton = MakeButton("Message a parent");
        messageButton.Click += (_, _) => OpenHub("/family/member/");
        _toggleUnlockButton.Text = "A parent can unlock…";
        StyleButton(_toggleUnlockButton);
        _toggleUnlockButton.Click += (_, _) => { _unlockPanel.Visible = !_unlockPanel.Visible; _error.Visible = false; };
        var closeNote = new Label { Text = "", AutoSize = true }; // keeps the row honest if needed later

        _actionRow.Controls.Add(askButton);
        _actionRow.Controls.Add(messageButton);
        _actionRow.Controls.Add(_toggleUnlockButton);
        askButton.Location = new Point(0, 0);
        askButton.Width = askButton.Width + 24;
        messageButton.Location = new Point(askButton.Right + 16, 0);
        messageButton.Width = messageButton.Width + 24;
        _toggleUnlockButton.Location = new Point(messageButton.Right + 16, 0);
        _toggleUnlockButton.Width = _toggleUnlockButton.Width + 24;

        BuildUnlockPanel();

        layout.Controls.Add(new Panel(), 0, 0);          // breathing room above the title
        layout.Controls.Add(_title, 0, 1);
        layout.Controls.Add(_reason, 0, 2);
        layout.Controls.Add(_until, 0, 3);
        layout.Controls.Add(new Panel(), 0, 4);          // breathing room
        layout.Controls.Add(hubNote, 0, 5);
        layout.Controls.Add(_error, 0, 6);

        Controls.Add(layout);

        // Unlock + action row sit inside the breathing room row, bottom-left anchored.
        var bottomBar = new Panel { Height = 170, Dock = DockStyle.Bottom, Padding = new Padding(64, 12, 64, 0) };
        bottomBar.Controls.Add(_actionRow);
        _actionRow.SetBounds(0, 8, 900, 64);
        bottomBar.Controls.Add(_unlockPanel);
        _unlockPanel.SetBounds(0, 76, 700, 78);
        _unlockPanel.Visible = true;
        _unlockPanel.Height = 1;
        Controls.Add(bottomBar);
        bottomBar.BringToFront();

        _refresh.Tick += async (_, _) => await RefreshAsync();
        _refresh.Start();
        Shown += async (_, _) => await RefreshAsync();

        FormClosing += (sender, args) =>
        {
            // Only the refresh (unlocked) path or the X on a safety break may close this.
            if (args.CloseReason == CloseReason.UserClosing && !_allowingClose) args.Cancel = true;
        };
        KeyDown += (_, args) => { if (args.KeyCode == Keys.Escape) args.Handled = true; };
    }

    private bool _allowingClose;

    private static Button MakeButton(string text)
    {
        var button = new Button { Text = text, AutoSize = true, Height = 48, Padding = new Padding(16, 6, 16, 6) };
        StyleButton(button);
        return button;
    }

    private static void StyleButton(Button button)
    {
        button.FlatStyle = FlatStyle.Flat;
        button.BackColor = Color.FromArgb(30, 41, 59);
        button.ForeColor = Color.FromArgb(226, 232, 240);
        button.FlatAppearance.BorderColor = Color.FromArgb(71, 85, 105);
        button.FlatAppearance.BorderSize = 1;
        button.Font = new Font("Segoe UI", 11);
        button.Cursor = Cursors.Hand;
    }

    private void BuildUnlockPanel()
    {
        _unlockPanel.BackColor = Color.FromArgb(15, 23, 42);
        _unlockPanel.AutoSize = true;

        var label = new Label { Text = "A parent signs in:", AutoSize = true, ForeColor = Color.FromArgb(148, 163, 184), Font = new Font("Segoe UI", 10) };
        _user.PlaceholderText = "Parent username";
        _user.Width = 220;
        _user.BackColor = Color.FromArgb(30, 41, 59);
        _user.ForeColor = Color.FromArgb(226, 232, 240);
        _user.BorderStyle = BorderStyle.FixedSingle;
        _password.PlaceholderText = "Parent password";
        _password.PasswordChar = '●';
        _password.Width = 220;
        _password.BackColor = Color.FromArgb(30, 41, 59);
        _password.ForeColor = Color.FromArgb(226, 232, 240);
        _password.BorderStyle = BorderStyle.FixedSingle;
        _password.UseSystemPasswordChar = false;

        _unlockButton.Text = "Unlock";
        StyleButton(_unlockButton);
        _unlockButton.BackColor = Color.FromArgb(217, 119, 6);
        _unlockButton.ForeColor = Color.FromArgb(15, 23, 42);
        _unlockButton.FlatAppearance.BorderSize = 0;
        _unlockButton.Click += async (_, _) => await UnlockAsync();

        var note = new Label { Text = "The unlock is reported to the other parents.", AutoSize = true, ForeColor = Color.FromArgb(100, 116, 139), Font = new Font("Segoe UI", 9) };

        label.Location = new Point(0, 0);
        _user.Location = new Point(0, 24);
        _password.Location = new Point(_user.Right + 8, 24);
        _unlockButton.Location = new Point(_password.Right + 8, 22);
        note.Location = new Point(0, 58);
        _unlockPanel.Controls.AddRange(new Control[] { label, _user, _password, _unlockButton, note });
        _unlockPanel.Height = 78;
    }

    private async Task RefreshAsync()
    {
        var state = await PipeProtocol.SendAsync(Config.PipeName, new PipeProtocol.Request { Cmd = "status" });
        if (state?.Ok == true && state.State is { } s)
        {
            _reason.Text = ReasonText(s.Reason);
            _until.Text = s.Until is null ? "" : $"…until {s.Until}";
            if (!s.Locked && _title.Tag is null)
            {
                _allowingClose = true;
                Close();
            }
            return;
        }
        _reason.Text = "Locked - cannot reach the hub";
        _until.Text = "";
    }

    private async Task UnlockAsync()
    {
        _error.Visible = false;
        var user = _user.Text.Trim();
        if (string.IsNullOrWhiteSpace(user) || string.IsNullOrEmpty(_password.Text))
        {
            _error.Text = "Enter the parent's username and password.";
            _error.Visible = true;
            return;
        }
        _unlockButton.Enabled = false;
        try
        {
            var response = await PipeProtocol.SendAsync(Config.PipeName, new PipeProtocol.Request
            {
                Cmd = "parent-unlock",
                ParentUsername = user,
                ParentPassword = _password.Text,
            }, timeoutMs: 15000);

            if (response?.Ok == true)
            {
                _allowingClose = true;
                Close();
                return;
            }
            _error.Text = response?.Error ?? "No answer - the guardian helper is not running.";
            _error.Visible = true;
        }
        finally { _unlockButton.Enabled = true; }
    }

    private void OpenHub(string suffix)
    {
        var state = Config.ReadState(); // tray pre-wrote hubUrl here; overlay runs as the child
        var hub = state?.HubUrl;
        if (string.IsNullOrWhiteSpace(hub)) { _error.Text = "The hub address is not configured."; _error.Visible = true; return; }
        var target = suffix.EndsWith("/") ? $"{hub}{suffix}{Uri.EscapeDataString(state?.Username ?? "")}" : $"{hub}{suffix}";
        try { Process.Start(new ProcessStartInfo(target) { UseShellExecute = true }); }
        catch { _error.Text = "Could not open the family hub."; _error.Visible = true; }
    }

    private static string ReasonText(string? reason) => reason switch
    {
        "bedtime" => "Bedtime",
        "school" => "School hours",
        "paused" => "Paused by a parent",
        "chores" => "Chores first",
        "limit" => "That's enough screen time for today",
        _ => "Locked",
    };
}
