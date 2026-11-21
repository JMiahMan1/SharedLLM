using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace JarvisGuard.Core;

/// <summary>
/// A parent's offline unlock code, checked on the PC with no connection: the
/// same scheme as the phone (services/ui/android OfflineCode.java and
/// GuardApi.offlineUnlock). The PC holds only PBKDF2-SHA256 verifiers (salt,
/// iterations, hash) that arrived inside a real online parent unlock; the
/// code itself never leaves the parent. A good code lifts the lock for ten
/// minutes.
///
/// Wrong codes are rate limited and the limit grows: five wrong codes lock
/// code entry for ten minutes, the next five for twenty, then forty, up to a
/// day; only a good code resets it. The SYSTEM agent keeps all of it on disk
/// (offline.codes.json, SYSTEM and Administrators only), so closing the lock
/// surface, killing the tray or restarting the PC resets nothing.
///
/// Nothing here is silent: a good code is reported (<c>offline_unlock</c>,
/// with whose code it was) and so are wrong codes (<c>offline_code_wrong</c>,
/// with how many). Reports wait in <see cref="Pending"/> while the hub cannot
/// be reached and are sent once it answers.
/// </summary>
public sealed class OfflineCodes
{
    public const int MaxFails = 5;
    /// <summary>The first lockout; each further one in a row doubles, up to <see cref="MaxLockout"/>.</summary>
    public static readonly TimeSpan FailWindow = TimeSpan.FromMinutes(10);
    public static readonly TimeSpan MaxLockout = TimeSpan.FromHours(24);
    public static readonly TimeSpan UnlockFor = TimeSpan.FromMinutes(10);

    /// <summary>The tamper events queued for the hub (services/gateway _TAMPER_EVENTS).</summary>
    public const string UnlockEvent = "offline_unlock";
    public const string WrongCodeEvent = "offline_code_wrong";

    public sealed class Verifier
    {
        public string Parent { get; set; } = "";
        public string Salt { get; set; } = "";
        public int Iterations { get; set; }
        public string Hash { get; set; } = "";
    }

    /// <summary>
    /// A report the hub has not had yet: <see cref="UnlockEvent"/> (Detail is
    /// whose code) or <see cref="WrongCodeEvent"/> (Detail is "3 wrong codes").
    /// </summary>
    public sealed class Report
    {
        public string Event { get; set; } = "";
        public string Detail { get; set; } = "";
        public DateTimeOffset At { get; set; }
    }

    public List<Verifier> Codes { get; set; } = new();
    /// <summary>Wrong codes since the last lockout or good code.</summary>
    public List<DateTimeOffset> Fails { get; set; } = new();
    /// <summary>Lockouts in a row (no good code between them): sets how long the next one lasts.</summary>
    public int Lockouts { get; set; }
    /// <summary>Code entry is refused until then.</summary>
    public DateTimeOffset? LockedUntil { get; set; }
    /// <summary>Reports waiting for the hub, oldest first.</summary>
    public List<Report> Pending { get; set; } = new();

    /// <summary>An offline unlock happened and the hub has not been told yet.</summary>
    [JsonIgnore]
    public bool ReportDue => Pending.Any(r => r.Event == UnlockEvent);

    /// <summary>
    /// Files from 0.2.0 kept a single "reportDue" flag. Read once for
    /// compatibility (it becomes a pending report of an unknown parent's code);
    /// never written.
    /// </summary>
    [JsonPropertyName("reportDue")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public bool? LegacyReportDue
    {
        get => null;
        set
        {
            if (value == true && !ReportDue) Pending.Add(new Report { Event = UnlockEvent, At = DateTimeOffset.Now });
        }
    }

    /// <summary>Ok, or why not; <see cref="Parent"/> is whose code matched.</summary>
    public readonly record struct Result(bool Ok, string? Error, string? Parent = null);

    /// <summary>Keep the verifiers an online parent unlock returned (<c>offline_codes</c>).</summary>
    public void Remember(JsonElement offlineCodes)
    {
        if (offlineCodes.ValueKind != JsonValueKind.Array) return;
        var codes = new List<Verifier>();
        foreach (var c in offlineCodes.EnumerateArray())
        {
            if (c.ValueKind != JsonValueKind.Object) continue;
            codes.Add(new Verifier
            {
                Parent = Str(c, "parent"),
                Salt = Str(c, "salt"),
                Iterations = c.TryGetProperty("iterations", out var i) && i.ValueKind == JsonValueKind.Number ? i.GetInt32() : 0,
                Hash = Str(c, "hash"),
            });
        }
        Codes = codes;
    }

    /// <summary>How long a lockout lasts after <paramref name="earlierLockouts"/> in a row.</summary>
    public static TimeSpan LockoutFor(int earlierLockouts)
    {
        var minutes = FailWindow.TotalMinutes * Math.Pow(2, Math.Clamp(earlierLockouts, 0, 16));
        return TimeSpan.FromMinutes(Math.Min(minutes, MaxLockout.TotalMinutes));
    }

    public Result TryUnlock(string? code, DateTimeOffset now)
    {
        if (LockedUntil is { } until && now < until)
            return new Result(false, $"Too many tries. Wait until {until.ToLocalTime():t}, then try again.");
        if (Codes.Count == 0)
            return new Result(false, "No offline code is set up on this PC yet. A parent signs in here once while it is online to set it up.");
        var typed = (code ?? "").Trim();
        var match = Codes.FirstOrDefault(c => Matches(typed, c.Salt, c.Iterations, c.Hash));
        if (match is not null)
        {
            Fails.Clear();
            Lockouts = 0;
            LockedUntil = null;
            Pending.Add(new Report { Event = UnlockEvent, Detail = match.Parent, At = now });
            return new Result(true, null, match.Parent);
        }
        Fails.Add(now);
        CountWrongCode(now);
        if (Fails.Count < MaxFails) return new Result(false, "That code is not right.");
        LockedUntil = now + LockoutFor(Lockouts);
        Lockouts++;
        Fails.Clear();
        return new Result(false, $"That code is not right. Too many tries: wait until {LockedUntil.Value.ToLocalTime():t}, then try again.");
    }

    /// <summary>Wrong codes the hub has not heard about are one report with a running count, not one per try.</summary>
    private void CountWrongCode(DateTimeOffset now)
    {
        var waiting = Pending.FirstOrDefault(r => r.Event == WrongCodeEvent);
        if (waiting is null)
        {
            Pending.Add(new Report { Event = WrongCodeEvent, Detail = WrongCodesText(1), At = now });
            return;
        }
        waiting.Detail = WrongCodesText(WrongCount(waiting) + 1);
    }

    private static string WrongCodesText(int n) => n == 1 ? "1 wrong code" : $"{n} wrong codes";

    /// <summary>The count a wrong-code report carries ("3 wrong codes" is 3).</summary>
    public static int WrongCount(Report r) =>
        int.TryParse(r.Detail.Split(' ', 2)[0], out var n) && n > 0 ? n : 1;

    /// <summary>
    /// The hub took <paramref name="report"/> as it read when it was sent
    /// (<paramref name="sentDetail"/>): it leaves the queue. Wrong codes typed
    /// while it was on its way stay queued, as their own count.
    /// </summary>
    public void Sent(Report report, string sentDetail)
    {
        if (report.Event == WrongCodeEvent && report.Detail != sentDetail)
        {
            var left = WrongCount(report) - WrongCount(new Report { Detail = sentDetail });
            if (left > 0)
            {
                report.Detail = WrongCodesText(left);
                return;
            }
        }
        Pending.Remove(report);
    }

    /// <summary>True when <paramref name="code"/> matches the verifier. Malformed verifiers never match.</summary>
    public static bool Matches(string code, string saltHex, int iterations, string hashHex)
    {
        if (string.IsNullOrEmpty(code) || iterations < 1 || iterations > 2_000_000) return false;
        try
        {
            var expected = Convert.FromHexString(hashHex);
            if (expected.Length == 0) return false;
            var actual = Rfc2898DeriveBytes.Pbkdf2(Encoding.UTF8.GetBytes(code), Convert.FromHexString(saltHex),
                iterations, HashAlgorithmName.SHA256, expected.Length);
            return CryptographicOperations.FixedTimeEquals(expected, actual);
        }
        catch { return false; }
    }

    public string Serialize() => JsonSerializer.Serialize(this, JsonOptions.Web);

    /// <summary>
    /// The kept state. No file is a fresh start. An unreadable file throws
    /// rather than reading as empty: an empty start would also wipe the
    /// lockout, which is exactly what damaging the file would buy.
    /// </summary>
    public static OfflineCodes Parse(string? json)
    {
        if (string.IsNullOrWhiteSpace(json)) return new OfflineCodes();
        try
        {
            return JsonSerializer.Deserialize<OfflineCodes>(json, JsonOptions.Web)
                ?? throw new InvalidDataException("offline.codes.json holds null.");
        }
        catch (JsonException ex)
        {
            throw new InvalidDataException($"offline.codes.json is not valid JSON: {ex.Message}", ex);
        }
    }

    /// <summary>
    /// What the agent starts with when the kept file cannot be read: no codes,
    /// so no offline unlock at all (nothing to guess against) until the hub
    /// hands the verifiers over again. The safe side, and the agent logs why.
    /// </summary>
    public static OfflineCodes Unreadable() => new();

    private static string Str(JsonElement e, string key) =>
        e.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : "";
}
