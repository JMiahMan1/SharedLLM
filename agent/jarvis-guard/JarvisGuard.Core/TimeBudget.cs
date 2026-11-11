namespace JarvisGuard.Core;

/// <summary>
/// Today's time as the PC should show it: the hub's answer, plus the minutes
/// this PC used since it last reported them (the hub cannot count those yet).
/// The warnings count down to <see cref="Result.NextLockAt"/>, so the day's
/// limit is re-timed from the time actually left. Pure.
/// </summary>
public static class TimeBudget
{
    public readonly record struct Result(int UsedMinutes, int RemainingMinutes, int? AllowedMinutes,
        string? NextLockAt, string? NextLockReason);

    public static Result Apply(HubStatus hub, int pendingMinutes, DateTimeOffset now)
    {
        var pending = Math.Max(0, pendingMinutes);
        var used = Math.Max(0, hub.UsedMinutes) + pending;
        int? allowed = hub.LimitMinutes is null
            ? null
            : Math.Max(0, hub.LimitMinutes.Value) + Math.Max(0, hub.BonusMinutes)
              + Math.Max(0, hub.EarnedMinutes) + Math.Max(0, hub.BankedMinutes);
        var remaining = allowed is null ? Math.Max(0, hub.RemainingMinutes) : Math.Max(0, hub.RemainingMinutes - pending);

        var nextAt = hub.NextLockAt;
        var nextReason = hub.NextLockReason;
        if (!hub.Locked && allowed is not null)
        {
            var limitAt = now.AddMinutes(remaining);
            var hubNext = DateTimeOffset.TryParse(hub.NextLockAt, out var parsed) ? parsed : (DateTimeOffset?)null;
            if (hub.NextLockReason == "limit" || hubNext is null || limitAt < hubNext)
            {
                nextAt = limitAt.ToString("O");
                nextReason = "limit";
            }
        }
        return new Result(used, remaining, allowed, nextAt, nextReason);
    }

    /// <summary>Close to the limit, the PC reports every minute so the hub locks on time.</summary>
    public static bool ReportNow(Result r) => r.AllowedMinutes is not null && r.RemainingMinutes <= 15;
}
