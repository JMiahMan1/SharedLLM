using System.Diagnostics;
using System.Text.Json;
using JarvisGuard.Core;

namespace JarvisGuard.Agent;

/// <summary>
/// Foreground-app meter: what the PC is doing, accumulated per executable and
/// posted to the hub as the same shape of usage report the phone posts. The
/// tray samples the desktop (see Tray.ForegroundSampler) and hands the
/// minutes over the pipe; this keeps the day's total and reports it.
///
/// Like the phone, each report is the <em>day's running total</em> for this
/// PC: the hub keeps the largest reading per device per day, so a report of
/// just the last half hour would be lost under an earlier, bigger one. The
/// total is kept on disk so a restart mid-day carries on from it.
/// </summary>
public sealed class UsageMeter
{
    private const int FlushEverySeconds = 1800;
    /// <summary>The most one report from the tray may add: it reports every 30 s.</summary>
    private const long MaxReportMs = 90_000;


    /// <summary>One day of this PC's use: what is posted, and what is kept on disk.</summary>
    private sealed class DayTotal
    {
        public string Day { get; set; } = "";
        public long TotalMs { get; set; }
        public long? FirstUseMs { get; set; }
        public long? LastUseMs { get; set; }
        public Dictionary<string, long> AppsMs { get; set; } = new(StringComparer.OrdinalIgnoreCase);
        /// <summary>How much of <see cref="TotalMs"/> the hub has already been told.</summary>
        public long SentMs { get; set; }
    }

    private readonly object _lock = new();
    private readonly string? _path;
    private DayTotal _today;
    /// <summary>Yesterday's last minutes, when the day turned over before they were sent.</summary>
    private DayTotal? _unsentYesterday;
    private long _lastFlush = Stopwatch.GetTimestamp();

    public UsageMeter(string? path = null)
    {
        _path = path;
        _today = Load(path) is { } saved && saved.Day == Today() ? saved : new DayTotal { Day = Today() };
    }

    public string? DeviceKey { get; set; }

    /// <summary>
    /// Foreground minutes the tray measured in the signed-in session (a
    /// SYSTEM service cannot see the desktop), per executable.
    /// </summary>
    public void Add(IReadOnlyDictionary<string, long>? appsMs)
    {
        if (appsMs is null || appsMs.Count == 0) return;
        lock (_lock)
        {
            RollOverIfNewDay();
            long budget = MaxReportMs;
            foreach (var (exe, raw) in appsMs)
            {
                if (string.IsNullOrWhiteSpace(exe) || raw <= 0 || budget <= 0) continue;
                var ms = Math.Min(raw, budget);
                budget -= ms;
                var name = exe.Trim().ToLowerInvariant();
                if (name.Length > 128) name = name[..128];
                _today.AppsMs.TryGetValue(name, out var had);
                _today.AppsMs[name] = had + ms;
                _today.TotalMs += ms;
            }
            var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            _today.FirstUseMs ??= now;
            _today.LastUseMs = now;
            Save();
        }
    }

    public bool DueForFlush => Stopwatch.GetElapsedTime(_lastFlush) > TimeSpan.FromSeconds(FlushEverySeconds);

    /// <summary>Whole minutes used today that the hub has not been told yet.</summary>
    public int PendingMinutes
    {
        get
        {
            lock (_lock)
            {
                RollOverIfNewDay();
                return (int)(Math.Max(0, _today.TotalMs - _today.SentMs) / 60000);
            }
        }
    }

    /// <summary>Whether anything was used since the last report.</summary>
    public bool HasUnsent
    {
        get { lock (_lock) return _unsentYesterday is not null || _today.TotalMs > _today.SentMs; }
    }

    /// <summary>
    /// The reports to post: the day's running total (and yesterday's, when the
    /// day turned over with minutes still unsent). Marks them sent and saves.
    /// </summary>
    public IReadOnlyList<object> Snapshot()
    {
        lock (_lock)
        {
            RollOverIfNewDay();
            var payloads = new List<object>();
            if (_unsentYesterday is not null) payloads.Add(Payload(_unsentYesterday));
            _unsentYesterday = null;
            payloads.Add(Payload(_today));
            _today.SentMs = _today.TotalMs;
            _lastFlush = Stopwatch.GetTimestamp();
            Save();
            return payloads;
        }
    }

    private object Payload(DayTotal d) => new
    {
        day = d.Day,
        millis = d.TotalMs,
        first_use = d.FirstUseMs,
        last_use = d.LastUseMs,
        sessions = 1,
        apps = d.AppsMs
            .OrderByDescending(kv => kv.Value)
            .Take(50)
            .Select(kv => new { package = kv.Key, millis = kv.Value })
            .ToArray(),
        source = "windows",
        device_key = DeviceKey,
    };

    private void RollOverIfNewDay()
    {
        var today = Today();
        if (_today.Day == today) return;
        if (_today.TotalMs > _today.SentMs) _unsentYesterday = _today;
        _today = new DayTotal { Day = today };
    }

    private static string Today() => DateTime.Now.ToString("yyyy-MM-dd");

    private void Save()
    {
        if (_path is null) return;
        try
        {
            var tmp = _path + ".tmp";
            File.WriteAllText(tmp, JsonSerializer.Serialize(_today, JsonOptions.Web));
            File.Move(tmp, _path, overwrite: true);
        }
        catch { /* a lost total costs at most the minutes since the last report */ }
    }

    private static DayTotal? Load(string? path)
    {
        try
        {
            if (path is null || !File.Exists(path)) return null;
            var saved = JsonSerializer.Deserialize<DayTotal>(File.ReadAllText(path), JsonOptions.Web);
            if (saved is null) return null;
            saved.AppsMs = new Dictionary<string, long>(saved.AppsMs, StringComparer.OrdinalIgnoreCase);
            return saved;
        }
        catch { return null; }
    }
}
