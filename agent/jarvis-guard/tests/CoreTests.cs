using System.Text.Json;
using JarvisGuard.Core;
using Xunit;

namespace JarvisGuard.Tests;

public class ConfigTests
{
    // Exactly what install.ps1 writes: camelCase keys, and Windows PowerShell's
    // Set-Content -Encoding UTF8 puts a byte order mark in front.
    private const string InstallerConfig = "﻿{\n  \"hubUrl\": \"https://hub.example.test\",\n  \"username\": \"kid\",\n" +
        "  \"agentKey\": \"wg_fake\",\n  \"deviceKey\": \"windows-test-guid\",\n  \"version\": \"0.2.0\"\n}";

    [Fact]
    public void ReadsTheConfigTheInstallerWrites()
    {
        var c = Config.ParseConfig(InstallerConfig);
        Assert.NotNull(c);
        Assert.Equal("https://hub.example.test", c!.HubUrl);
        Assert.Equal("kid", c.Username);
        Assert.Equal("wg_fake", c.AgentKey);
        Assert.Equal("windows-test-guid", c.DeviceKey);
        Assert.Equal("0.2.0", c.Version);
    }

    [Fact]
    public void AConfigWithoutAKeyIsNoConfig()
    {
        Assert.Null(Config.ParseConfig("{\"hubUrl\":\"https://hub.example.test\",\"username\":\"kid\"}"));
        Assert.Null(Config.ParseConfig("not json"));
    }

    [Fact]
    public void TheCacheKeepsGrantedUntilBesideTheRules()
    {
        var (rules, granted) = Config.ParseRawCache(
            "{\"rules\":{\"bedtime_start\":\"21:00\"},\"granted_until\":\"2026-01-05T22:00:00+00:00\",\"locked\":false}");
        Assert.NotNull(rules);
        Assert.Equal(new DateTimeOffset(2026, 1, 5, 22, 0, 0, TimeSpan.Zero), granted);
    }
}

public class GuardEvaluatorTests
{
    private static readonly TimeSpan Zone = TimeSpan.FromHours(-7);
    private static DateTimeOffset At(int day, int h, int m) => new(2026, 1, day, h, m, 0, Zone); // 2026-01-05 is a Monday

    private static JsonElement Rules(string json) => JsonDocument.Parse(json).RootElement.Clone();

    [Theory]
    [InlineData(5, 21, 30, true)]
    [InlineData(6, 6, 59, true)]
    [InlineData(6, 7, 0, false)]
    [InlineData(5, 20, 59, false)]
    public void BedtimeRunsOvernight(int day, int h, int m, bool locked)
    {
        var d = GuardEvaluator.Evaluate(Rules("{\"bedtime_start\":\"21:00\",\"bedtime_end\":\"07:00\"}"), At(day, h, m));
        Assert.Equal(locked, d.Locked);
        if (locked) Assert.Equal("bedtime", d.Reason);
    }

    [Fact]
    public void ReadsAnHourWithoutALeadingZeroLikeTheHub()
    {
        var d = GuardEvaluator.Evaluate(Rules("{\"bedtime_start\":\"21:00\",\"bedtime_end\":\"7:30\"}"), At(6, 7, 15));
        Assert.True(d.Locked);
    }

    [Fact]
    public void TimeAParentGaveLiftsBedtimeUntilItRunsOut()
    {
        var rules = Rules("{\"bedtime_start\":\"21:00\",\"bedtime_end\":\"07:00\"}");
        Assert.False(GuardEvaluator.Evaluate(rules, At(5, 21, 30), At(5, 22, 0)).Locked);
        Assert.True(GuardEvaluator.Evaluate(rules, At(5, 22, 30), At(5, 22, 0)).Locked);
    }

    [Fact]
    public void AParentsPauseWinsOverGivenTime()
    {
        var d = GuardEvaluator.Evaluate(Rules("{\"paused\":true}"), At(5, 12, 0), At(5, 13, 0));
        Assert.Equal(new GuardEvaluator.Decision(true, "paused"), d);
    }

    [Fact]
    public void APauseUntilATimeHoldsOnlyUntilThen()
    {
        var rules = Rules("{\"paused_until\":\"2026-01-05T13:00:00-07:00\"}");
        Assert.True(GuardEvaluator.Evaluate(rules, At(5, 12, 0)).Locked);
        Assert.False(GuardEvaluator.Evaluate(rules, At(5, 13, 1)).Locked);
    }

    [Fact]
    public void SchoolHoursOnlyOnSchoolDays()
    {
        var rules = Rules("{\"school_start\":\"08:00\",\"school_end\":\"15:00\"}");
        Assert.Equal("school", GuardEvaluator.Evaluate(rules, At(5, 10, 0)).Reason); // Monday
        Assert.False(GuardEvaluator.Evaluate(rules, At(10, 10, 0)).Locked);         // Saturday
        var weekend = Rules("{\"school_start\":\"08:00\",\"school_end\":\"15:00\",\"school_days\":[\"sat\"]}");
        Assert.True(GuardEvaluator.Evaluate(weekend, At(10, 10, 0)).Locked);
    }

    [Fact]
    public void NoRulesOrAnEmptyWindowNeverLock()
    {
        Assert.False(GuardEvaluator.Evaluate(null, At(5, 23, 0)).Locked);
        Assert.False(GuardEvaluator.Evaluate(Rules("{\"bedtime_start\":\"21:00\",\"bedtime_end\":\"21:00\"}"), At(5, 21, 0)).Locked);
    }
}

public class LockEnforcerTests
{
    private static readonly DateTimeOffset T0 = new(2026, 1, 5, 21, 0, 0, TimeSpan.Zero);

    [Fact]
    public void TheChildSeesTheLockSurfaceBeforeTheSignOut()
    {
        var e = new LockEnforcer(TimeSpan.FromMinutes(2), TimeSpan.FromSeconds(45));
        Assert.Empty(e.SessionsToSignOut(true, new uint[] { 3 }, T0));
        Assert.Equal(T0.AddMinutes(2), e.NextSignOutAt(true));
        Assert.Empty(e.SessionsToSignOut(true, new uint[] { 3 }, T0.AddSeconds(90)));
        Assert.Equal(new uint[] { 3 }, e.SessionsToSignOut(true, new uint[] { 3 }, T0.AddMinutes(2)));
    }

    [Fact]
    public void SigningBackInDuringTheLockIsSignedOutAgain()
    {
        // The regression: the old agent signed out once per lock, so a child
        // who signed straight back in kept the PC until the lock ended.
        var e = new LockEnforcer(TimeSpan.FromMinutes(2), TimeSpan.FromSeconds(45));
        e.SessionsToSignOut(true, new uint[] { 3 }, T0);
        Assert.Single(e.SessionsToSignOut(true, new uint[] { 3 }, T0.AddMinutes(2)));
        var back = T0.AddMinutes(3);
        Assert.Empty(e.SessionsToSignOut(true, new uint[] { 4 }, back));
        Assert.Equal(new uint[] { 4 }, e.SessionsToSignOut(true, new uint[] { 4 }, back.AddSeconds(45)));
    }

    [Fact]
    public void AParentsUnlockHoldsWhileTheHubStillSaysLocked()
    {
        // The regression: the hub keeps reporting bedtime after a parent's
        // unlock, and the old agent signed the session out on the next poll.
        var e = new LockEnforcer(TimeSpan.FromMinutes(2), TimeSpan.FromSeconds(45));
        e.SessionsToSignOut(true, new uint[] { 3 }, T0);
        e.ParentUnlocked(T0.AddMinutes(10));
        var now = T0.AddMinutes(5);
        Assert.False(e.EffectiveLocked(true, now));
        Assert.Empty(e.SessionsToSignOut(e.EffectiveLocked(true, now), new uint[] { 3 }, now));

        var after = T0.AddMinutes(10);
        Assert.True(e.EffectiveLocked(true, after));
        Assert.Empty(e.SessionsToSignOut(true, new uint[] { 3 }, after)); // a fresh grace, not an instant sign-out
        Assert.Single(e.SessionsToSignOut(true, new uint[] { 3 }, after.AddMinutes(2)));
    }

    [Fact]
    public void LiftingTheLockForgetsTheCountdown()
    {
        var e = new LockEnforcer(TimeSpan.FromMinutes(2), TimeSpan.FromSeconds(45));
        e.SessionsToSignOut(true, new uint[] { 3 }, T0);
        Assert.Empty(e.SessionsToSignOut(false, new uint[] { 3 }, T0.AddMinutes(1)));
        Assert.Null(e.NextSignOutAt(false));
        Assert.Empty(e.SessionsToSignOut(true, new uint[] { 3 }, T0.AddMinutes(3)));
    }

    [Fact]
    public void NobodySignedInIsNothingToDo()
    {
        var e = new LockEnforcer();
        Assert.Empty(e.SessionsToSignOut(true, Array.Empty<uint>(), T0));
        Assert.Null(e.NextSignOutAt(true));
    }
}

public class HubStatusTests
{
    [Fact]
    public void ParsesTheGatewaysStatusAnswer()
    {
        // The shape of GET /api/screen-time/{u}/status (services/gateway/main.py
        // + services/common/screen_time.py evaluate()), trimmed.
        const string json = "{\"status\":\"SUCCESS\",\"username\":\"kid\",\"rules\":{\"bedtime_start\":\"21:00\"}," +
            "\"theme_id\":\"clean-athletic\",\"wipe_requested\":false,\"limit_min\":120,\"used_min\":30,\"bonus_min\":10," +
            "\"earned_min\":5,\"banked_min\":0,\"remaining_min\":105,\"locked\":false,\"reason\":null,\"until\":null," +
            "\"granted_until\":null,\"next_lock_at\":\"2026-01-05T21:00:00-07:00\",\"next_lock_reason\":\"bedtime\",\"notices\":[]}";
        var s = HubStatus.Parse(json);
        Assert.False(s.Locked);
        Assert.Equal(120, s.LimitMinutes);
        Assert.Equal(105, s.RemainingMinutes);
        Assert.Equal("bedtime", s.NextLockReason);
        Assert.NotNull(s.Rules);
    }

    [Fact]
    public void NoLimitIsNullNotZero()
    {
        var s = HubStatus.Parse("{\"locked\":true,\"reason\":\"bedtime\",\"limit_min\":null,\"remaining_min\":null}");
        Assert.True(s.Locked);
        Assert.Null(s.LimitMinutes);
        Assert.Equal(0, s.RemainingMinutes);
    }
}

public class TimeBudgetAndWarningTests
{
    [Fact]
    public void UnsentMinutesComeOffWhatIsLeft()
    {
        var hub = new HubStatus { LimitMinutes = 60, UsedMinutes = 40, RemainingMinutes = 20 };
        var now = new DateTimeOffset(2026, 1, 5, 16, 0, 0, TimeSpan.Zero);
        var r = TimeBudget.Apply(hub, 8, now);
        Assert.Equal(48, r.UsedMinutes);
        Assert.Equal(12, r.RemainingMinutes);
        Assert.Equal("limit", r.NextLockReason);
        Assert.True(TimeBudget.ReportNow(r));
    }

    [Fact]
    public void EachWarningShowsOnce()
    {
        var t = new LockWarning.Tracker();
        var now = new DateTimeOffset(2026, 1, 5, 20, 50, 0, TimeSpan.Zero);
        var at = now.AddMinutes(10);
        var first = t.Next(now, at, "bedtime");
        Assert.NotNull(first);
        Assert.True(first!.Dialog);
        Assert.Null(t.Next(now.AddSeconds(30), at, "bedtime"));
        Assert.NotNull(t.Next(now.AddMinutes(5), at, "bedtime"));
    }
}
