using System.Text.Json;

namespace JarvisGuard.Core;

/// <summary>
/// The day's limit when the hub cannot be reached. Before this the offline
/// path re-checked only the clock rules (pause, bedtime, school) and the
/// day's limit failed open: unplugging the router was unlimited time.
///
/// Every good poll keeps a <see cref="Snapshot"/>: the day, the time the hub
/// allowed in total, the minutes it said were left (less what this PC had not
/// reported yet) and this PC's own running total at that moment. Offline, the
/// minutes this PC has counted since are taken off what was left. On a new
/// day with no answer from the hub yet, the cached rules' limit for that day
/// applies against this PC's own count (bonus, earned and banked time are the
/// hub's to work out, so none is assumed). Pure.
/// </summary>
public static class OfflineLimit
{
    public sealed class Snapshot
    {
        /// <summary>Local day, yyyy-MM-dd.</summary>
        public string Day { get; set; } = "";
        /// <summary>Everything allowed that day; null with no limit.</summary>
        public int? AllowedMinutes { get; set; }
        /// <summary>Left at the poll, after this PC's unreported minutes.</summary>
        public int RemainingMinutes { get; set; }
        /// <summary>This PC's running total for the day at the poll.</summary>
        public long MeterMs { get; set; }
    }

    public readonly record struct Result(int? AllowedMinutes, int? RemainingMinutes);

    /// <summary>
    /// What is allowed and left now. <paramref name="today"/> is the local day
    /// (yyyy-MM-dd) and <paramref name="meterMsToday"/> this PC's running total
    /// for it.
    /// </summary>
    public static Result Remaining(Snapshot? snapshot, JsonElement? cachedRules, string today, DayOfWeek weekday, long meterMsToday)
    {
        var meter = Math.Max(0, meterMsToday);
        if (snapshot is not null && snapshot.Day == today)
        {
            if (snapshot.AllowedMinutes is null) return new Result(null, null);
            var since = (int)(Math.Max(0, meter - snapshot.MeterMs) / 60000);
            return new Result(snapshot.AllowedMinutes, Math.Max(0, snapshot.RemainingMinutes - since));
        }
        var limit = GuardEvaluator.LimitFor(cachedRules, weekday);
        if (limit is null) return new Result(null, null);
        var allowed = Math.Max(0, limit.Value);
        return new Result(allowed, Math.Max(0, allowed - (int)(meter / 60000)));
    }

    public static string Serialize(Snapshot s) => JsonSerializer.Serialize(s, JsonOptions.Web);

    /// <summary>The kept snapshot; null for no file. A damaged file throws (the caller logs it).</summary>
    public static Snapshot? Parse(string? json)
    {
        if (string.IsNullOrWhiteSpace(json)) return null;
        try { return JsonSerializer.Deserialize<Snapshot>(json, JsonOptions.Web); }
        catch (JsonException ex) { throw new InvalidDataException($"limit.snapshot.json is not valid JSON: {ex.Message}", ex); }
    }
}
