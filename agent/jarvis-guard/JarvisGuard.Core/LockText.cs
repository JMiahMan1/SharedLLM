using System.Globalization;

namespace JarvisGuard.Core;

/// <summary>
/// The words on the lock surface, kept apart from the form so they can be
/// tested: a calm headline for the reason, when the lock lifts, and the
/// countdown to the sign-out. The same reasons as the phone's lock screen.
/// </summary>
public static class LockText
{
    public static string Title(string? reason) => reason switch
    {
        "bedtime" => "It's bedtime",
        "school" => "School hours",
        "paused" => "Paused by a parent",
        "chores" => "Chores first",
        "limit" => "That's all the screen time for today",
        _ => "This PC is locked",
    };

    public static string Until(string? reason, string? untilIso, IFormatProvider? culture = null)
    {
        culture ??= CultureInfo.CurrentCulture;
        if (reason == "chores") return "Tick off today's chores and it unlocks.";
        if (!DateTimeOffset.TryParse(untilIso, CultureInfo.InvariantCulture, DateTimeStyles.None, out var until))
            return reason == "paused" ? "Until a parent lifts it." : "";
        var local = until.ToLocalTime();
        if (local.TimeOfDay == TimeSpan.Zero) return "Until midnight.";
        return $"Until {local.ToString("t", culture)}.";
    }

    /// <summary>"Signing out in 1:45. Save your work." while a sign-out is coming, else nothing.</summary>
    public static string Countdown(string? signOutIso, DateTimeOffset now)
    {
        if (!DateTimeOffset.TryParse(signOutIso, CultureInfo.InvariantCulture, DateTimeStyles.None, out var at)) return "";
        var left = at - now;
        if (left <= TimeSpan.Zero) return "Signing out now.";
        var seconds = (int)Math.Ceiling(left.TotalSeconds);
        return $"Signing out in {seconds / 60}:{seconds % 60:00}. Save your work.";
    }
}
