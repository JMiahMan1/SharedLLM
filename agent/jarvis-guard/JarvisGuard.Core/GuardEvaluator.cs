using System.Text.Json;

namespace JarvisGuard.Core;

/// <summary>
/// Offline fallback for when the hub cannot be reached: the last cached rules
/// are re-evaluated against the local clock, the same clock the rule window
/// needs. The hub remains the authority; this only keeps last night's
/// bedtime locking when the internet drops.
/// </summary>
public static class GuardEvaluator
{
    public readonly record struct Decision(bool Locked, string? Reason);

    public static Decision Evaluate(JsonElement? rules, DateTimeOffset now)
    {
        if (rules is not { ValueKind: JsonValueKind.Object }) return new Decision(false, null);
        var r = rules.Value;

        // A parent-paused PC stays paused offline; a granted extension
        // outweighs it (same as the hub's evaluate()).
        if (Bool(r, "paused"))
        {
            if (UntilPassed(r, "granted_until", now)) return new Decision(false, null);
            return new Decision(true, "paused");
        }

        if (CheckActive(r, "bedtime", now)) return new Decision(true, "bedtime");
        if (CheckActive(r, "school", now)) return new Decision(true, "school");
        return new Decision(false, null);
    }

    private static bool CheckActive(JsonElement r, string key, DateTimeOffset now)
    {
        if (!r.TryGetProperty($"{key}_start", out var s) || !r.TryGetProperty($"{key}_end", out var e)) return false;
        if (!TryParseHHmm(s, out var start) || !TryParseHHmm(e, out var end)) return false;
        // School only on configured days (defaults to Mon..Fri, like the hub).
        if (key == "school")
        {
            var days = r.TryGetProperty("school_days", out var d) && d.ValueKind == JsonValueKind.Array
                ? d.EnumerateArray().Select(x => x.GetString() ?? "").ToHashSet(StringComparer.OrdinalIgnoreCase)
                : new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "mon", "tue", "wed", "thu", "fri" };
            var abbr = now.DayOfWeek.ToString()[..3].ToLowerInvariant();
            if (!days.Contains(abbr)) return false;
        }
        if (Bool(r, "paused")) return false;
        // A granted extension suspends bedtime/school until it runs out.
        if (!UntilPassed(r, "granted_until", now)) return false;
        var t = now.TimeOfDay;
        if (start <= end) return t >= start && t < end; // same-day window
        return t >= start || t < end;                   // overnight window (bedtime)
    }

    private static bool UntilPassed(JsonElement r, string key, DateTimeOffset now)
    {
        if (!r.TryGetProperty(key, out var u)) return true;
        if (u.ValueKind == JsonValueKind.Null) return true;
        if (u.ValueKind != JsonValueKind.String) return true;
        if (!DateTimeOffset.TryParse(u.GetString(), out var until)) return true;
        return now >= until;
    }

    private static bool Bool(JsonElement r, string key) =>
        r.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.True;

    private static bool TryParseHHmm(JsonElement e, out TimeSpan t)
    {
        t = TimeSpan.Zero;
        if (e.ValueKind != JsonValueKind.String) return false;
        if (!TimeOnly.TryParseExact(e.GetString(), "HH:mm", out var time)) return false;
        t = time.ToTimeSpan();
        return true;
    }
}
