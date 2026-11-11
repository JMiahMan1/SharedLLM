namespace JarvisGuard.Core;

/// <summary>
/// How today's screen time reads on the PC (tray tooltip, the time panel):
/// the same words as the phone's tile and widget. Pure.
/// </summary>
public static class TimeText
{
    /// <summary>"45m", "1h", "1h 20m".</summary>
    public static string Minutes(int min)
    {
        if (min < 0) min = 0;
        if (min < 60) return $"{min}m";
        var h = min / 60;
        var m = min % 60;
        return m == 0 ? $"{h}h" : $"{h}h {m}m";
    }

    public static bool Limited(GuardState s) => s.AllowedMinutes is > 0;

    /// <summary>The big line: "45m left today", "No time limit today", or why it is locked.</summary>
    public static string Headline(GuardState s)
    {
        if (s.Locked) return ReasonTitle(s.Reason);
        if (!Limited(s)) return "No time limit today";
        return $"{Minutes(s.RemainingMinutes)} left today";
    }

    /// <summary>"Used 1h 15m of 2h", or "Used 1h 15m today" without a limit.</summary>
    public static string UsedLine(GuardState s) =>
        Limited(s) ? $"Used {Minutes(s.UsedMinutes)} of {Minutes(s.AllowedMinutes ?? 0)}" : $"Used {Minutes(s.UsedMinutes)} today";

    /// <summary>Percent of today's allowed time already used (0-100).</summary>
    public static int UsedPercent(GuardState s)
    {
        var allowed = s.AllowedMinutes ?? 0;
        if (allowed <= 0) return 0;
        return Math.Clamp((int)Math.Round(s.UsedMinutes * 100.0 / allowed), 0, 100);
    }

    /// <summary>"Includes 20m earned, 15m from a parent", or "".</summary>
    public static string ExtraLine(GuardState s)
    {
        if (!Limited(s)) return "";
        var parts = new List<string>();
        if (s.EarnedMinutes > 0) parts.Add($"{Minutes(s.EarnedMinutes)} earned");
        if (s.BonusMinutes > 0) parts.Add($"{Minutes(s.BonusMinutes)} from a parent");
        if (s.BankedMinutes > 0) parts.Add($"{Minutes(s.BankedMinutes)} saved from earlier");
        return parts.Count == 0 ? "" : "Includes " + string.Join(", ", parts);
    }

    /// <summary>What happens next: "Bedtime at 9:15 PM", or "Until 7:00 AM" while locked.</summary>
    public static string NextLine(GuardState s)
    {
        if (s.Locked)
        {
            return DateTimeOffset.TryParse(s.Until, out var until)
                ? $"Until {until.ToLocalTime():h:mm tt}" : "Until a parent unlocks it";
        }
        if (!DateTimeOffset.TryParse(s.NextLockAt, out var next)) return "";
        var name = s.NextLockReason switch
        {
            "bedtime" => "Bedtime",
            "school" => "School",
            "chores" => "Chores first",
            "limit" => "Time runs out",
            _ => "Locks",
        };
        return $"{name} at {next.ToLocalTime():h:mm tt}";
    }

    /// <summary>The tray tooltip (Windows keeps 127 characters): "Screen time: 45m left · used 1h 15m".</summary>
    public static string Tooltip(GuardState? s)
    {
        if (s is null) return "This PC is managed by the family hub";
        string text;
        if (s.Locked) text = $"Locked: {ReasonTitle(s.Reason).ToLowerInvariant()}";
        else if (Limited(s)) text = $"Screen time: {Minutes(s.RemainingMinutes)} left · used {Minutes(s.UsedMinutes)}";
        else text = $"Screen time: used {Minutes(s.UsedMinutes)} today";
        text += "\nManaged by the family hub";
        return text.Length > 127 ? text[..127] : text;
    }

    public static string ReasonTitle(string? reason) => reason switch
    {
        "paused" => "Paused by a parent",
        "bedtime" => "It's bedtime",
        "school" => "School time",
        "chores" => "Chores first",
        "limit" => "Time's up for today",
        _ => "Locked",
    };
}
