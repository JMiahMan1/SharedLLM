namespace JarvisGuard.Core;

/// <summary>
/// Warnings before a lock, the same as the phone's (services/ui/android
/// LockWarning.java): ten and five minutes before bedtime, school, "chores
/// first" or the end of the day's screen time, a dialog that says how long is
/// left and offers "Ask for more time"; a minute before, a last heads-up.
/// Pure, so the tray can call it every few seconds.
/// </summary>
public static class LockWarning
{
    /// <summary>The warnings, largest first. The ones at or above <see cref="DialogMinutes"/> get a dialog.</summary>
    public static readonly int[] StagesMinutes = { 10, 5, 1 };
    public const int DialogMinutes = 5;
    private static readonly TimeSpan WarnWindow = TimeSpan.FromMinutes(10);
    /// <summary>The warnings start over once the lock is this far away again (more time was given, a new day).</summary>
    private static readonly TimeSpan ResetWindow = TimeSpan.FromMinutes(15);

    public sealed record Warning(string Key, string Title, string Text, int Minutes, int Stage)
    {
        /// <summary>Ten and five minutes out: a dialog with "Ask for more time". The last minute is a notice.</summary>
        public bool Dialog => Stage >= DialogMinutes;
    }

    /// <summary>The warning for the stage the clock is in when the next lock is ten minutes away or less, else null.</summary>
    public static Warning? Due(DateTimeOffset now, DateTimeOffset? nextLockAt, string? reason)
    {
        if (nextLockAt is null || string.IsNullOrEmpty(reason)) return null;
        var left = nextLockAt.Value - now;
        if (left <= TimeSpan.Zero || left > WarnWindow) return null;
        var minutes = (int)Math.Ceiling(left.TotalMinutes);
        var stage = StagesMinutes[0];
        foreach (var s in StagesMinutes) if (minutes <= s) stage = s;
        return new Warning($"{reason}:{stage}", Title(minutes), Text(reason, minutes), minutes, stage);
    }

    /// <summary>Which warnings were already shown for the lock coming up, so each stage is shown once.</summary>
    public sealed class Tracker
    {
        private readonly HashSet<string> _shown = new();

        public Warning? Next(DateTimeOffset now, DateTimeOffset? nextLockAt, string? reason)
        {
            if (nextLockAt is null || nextLockAt.Value <= now || nextLockAt.Value - now > ResetWindow)
            {
                _shown.Clear();
                return null;
            }
            var w = Due(now, nextLockAt, reason);
            if (w is null || !_shown.Add(w.Key)) return null;
            // A later stage seen first stands in for the earlier ones.
            foreach (var s in StagesMinutes) if (s > w.Stage) _shown.Add($"{reason}:{s}");
            return w;
        }
    }

    public static string Title(int minutes) => minutes <= 1 ? "One minute" : $"{minutes} minutes left";

    public static string Text(string reason, int minutes)
    {
        var inText = minutes <= 1 ? "one minute" : $"{minutes} minutes";
        var lead = minutes <= 1 ? "One minute" : $"{minutes} minutes";
        return reason switch
        {
            "bedtime" => $"Bedtime in {inText}. Time to finish up.",
            "school" => $"School time starts in {inText}.",
            "limit" => $"{lead} of screen time left today.",
            "chores" => $"{lead} left. Then it's chores first.",
            _ => $"This PC locks in {inText}.",
        };
    }
}
