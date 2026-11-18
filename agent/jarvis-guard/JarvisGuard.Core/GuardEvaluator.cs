using System.Text.Json;

namespace JarvisGuard.Core;

/// <summary>
/// Offline fallback for when the hub cannot be reached: the last cached rules
/// are re-evaluated against the local clock, the same clock the rule window
/// needs. The hub remains the authority; this only keeps last night's
/// bedtime locking when the internet drops.
///
/// Mirrors the order of services/common/screen_time.py evaluate(): a parent's
/// pause (on, or until a time) always wins; time a parent gave
/// (<c>granted_until</c>, which the hub answers next to the rules, not in
/// them) lifts bedtime and school until it runs out.
/// </summary>
public static class GuardEvaluator
{
    public readonly record struct Decision(bool Locked, string? Reason);

    private static readonly string[] SchoolDaysDefault = { "mon", "tue", "wed", "thu", "fri" };

    public static Decision Evaluate(JsonElement? rules, DateTimeOffset now, DateTimeOffset? grantedUntil = null)
    {
        if (rules is not { ValueKind: JsonValueKind.Object }) return new Decision(false, null);
        var r = rules.Value;

        if (Bool(r, "paused") || (TryUntil(r, "paused_until", out var pausedUntil) && now < pausedUntil))
            return new Decision(true, "paused");

        var granted = grantedUntil is { } g && now < g;
        if (!granted && InWindow(r, "bedtime", now)) return new Decision(true, "bedtime");
        if (!granted && IsSchoolDay(r, now) && InWindow(r, "school", now)) return new Decision(true, "school");
        return new Decision(false, null);
    }

    private static bool IsSchoolDay(JsonElement r, DateTimeOffset now)
    {
        var days = r.TryGetProperty("school_days", out var d) && d.ValueKind == JsonValueKind.Array && d.GetArrayLength() > 0
            ? d.EnumerateArray().Select(x => x.ValueKind == JsonValueKind.String ? x.GetString() ?? "" : "").ToArray()
            : SchoolDaysDefault;
        var today = now.DayOfWeek.ToString()[..3].ToLowerInvariant();
        return days.Any(x => string.Equals(x, today, StringComparison.OrdinalIgnoreCase));
    }

    private static bool InWindow(JsonElement r, string key, DateTimeOffset now)
    {
        if (!r.TryGetProperty($"{key}_start", out var s) || !r.TryGetProperty($"{key}_end", out var e)) return false;
        if (!TryParseHHmm(s, out var start) || !TryParseHHmm(e, out var end) || start == end) return false;
        var t = now.TimeOfDay;
        if (start < end) return t >= start && t < end; // same-day window
        return t >= start || t < end;                  // overnight window (bedtime)
    }

    private static bool TryUntil(JsonElement r, string key, out DateTimeOffset until)
    {
        until = default;
        if (!r.TryGetProperty(key, out var u) || u.ValueKind != JsonValueKind.String) return false;
        return DateTimeOffset.TryParse(u.GetString(), out until);
    }

    private static bool Bool(JsonElement r, string key) =>
        r.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.True;

    /// <summary>"21:30" or "7:30", like the hub's parse_hhmm; anything else is no window.</summary>
    public static bool TryParseHHmm(JsonElement e, out TimeSpan t)
    {
        t = TimeSpan.Zero;
        if (e.ValueKind != JsonValueKind.String) return false;
        var parts = (e.GetString() ?? "").Split(':', 2);
        if (parts.Length != 2 || !int.TryParse(parts[0], out var h) || !int.TryParse(parts[1], out var m)) return false;
        if (h is < 0 or > 23 || m is < 0 or > 59) return false;
        t = new TimeSpan(h, m, 0);
        return true;
    }
}
