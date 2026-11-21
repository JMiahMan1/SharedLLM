using System.Text;
using System.Text.Json;
using JarvisGuard.Core;
using Xunit;

namespace JarvisGuard.Tests;

public class OfflineCodeRateLimitTests
{
    // Made the way identity makes them: hashlib.pbkdf2_hmac("sha256", code, salt, iterations).hex().
    private const string Salt = "00112233445566778899aabbccddeeff";
    private const string Hash = "86e00dcfbb6bc368ad355e286a3ac9584a9722d4b3d2e760c21db92dcfd5e0f0";
    private static readonly DateTimeOffset T0 = new(2026, 1, 5, 22, 0, 0, TimeSpan.Zero);

    private static OfflineCodes WithCode()
    {
        var codes = new OfflineCodes();
        codes.Remember(JsonDocument.Parse(
            $"[{{\"parent\":\"parent-a\",\"salt\":\"{Salt}\",\"iterations\":1000,\"hash\":\"{Hash}\"}}]").RootElement);
        return codes;
    }

    private static void FailFive(OfflineCodes codes, DateTimeOffset at)
    {
        for (var i = 0; i < OfflineCodes.MaxFails; i++) codes.TryUnlock("000000", at);
    }

    [Fact]
    public void EachLockoutInARowLastsTwiceAsLong()
    {
        var codes = WithCode();
        FailFive(codes, T0);
        Assert.Equal(T0.AddMinutes(10), codes.LockedUntil);
        FailFive(codes, T0.AddMinutes(11));
        Assert.Equal(T0.AddMinutes(31), codes.LockedUntil);
        FailFive(codes, T0.AddMinutes(32));
        Assert.Equal(T0.AddMinutes(72), codes.LockedUntil);
        Assert.Equal(OfflineCodes.MaxLockout, OfflineCodes.LockoutFor(50));
    }

    [Fact]
    public void ALockoutRefusesEvenTheRightCodeAndSurvivesARestart()
    {
        var codes = WithCode();
        FailFive(codes, T0);
        // Restarting the agent (or killing the tray) reads the kept file again.
        var again = OfflineCodes.Parse(codes.Serialize());
        Assert.StartsWith("Too many tries", again.TryUnlock("246810", T0.AddMinutes(5)).Error);
        Assert.True(again.TryUnlock("246810", T0.AddMinutes(10)).Ok);
        Assert.Equal(0, again.Lockouts);
    }

    [Fact]
    public void AGoodCodeIsReportedWithWhoseCodeItWas()
    {
        var codes = WithCode();
        var r = codes.TryUnlock("246810", T0);
        Assert.Equal("parent-a", r.Parent);
        var report = Assert.Single(codes.Pending);
        Assert.Equal(OfflineCodes.UnlockEvent, report.Event);
        Assert.Equal("parent-a", report.Detail);
    }

    [Fact]
    public void WrongCodesAreOneQueuedReportWithACount()
    {
        var codes = WithCode();
        codes.TryUnlock("111111", T0);
        codes.TryUnlock("222222", T0);
        codes.TryUnlock("333333", T0);
        var report = Assert.Single(OfflineCodes.Parse(codes.Serialize()).Pending);
        Assert.Equal(OfflineCodes.WrongCodeEvent, report.Event);
        Assert.Equal("3 wrong codes", report.Detail);
    }

    [Fact]
    public void WrongCodesTypedWhileAReportIsOnItsWayStayQueued()
    {
        var codes = WithCode();
        codes.TryUnlock("111111", T0);
        codes.TryUnlock("222222", T0);
        var report = codes.Pending.Single();
        var sentDetail = report.Detail;      // "2 wrong codes" goes to the hub
        codes.TryUnlock("333333", T0);        // one more while it is sent
        codes.Sent(report, sentDetail);
        Assert.Equal("1 wrong code", Assert.Single(codes.Pending).Detail);
        codes.Sent(codes.Pending.Single(), "1 wrong code");
        Assert.Empty(codes.Pending);
    }

    [Fact]
    public void AnOldFilesReportDueBecomesAPendingReport()
    {
        var old = OfflineCodes.Parse("{\"codes\":[],\"fails\":[],\"reportDue\":true}");
        Assert.True(old.ReportDue);
        Assert.Equal(OfflineCodes.UnlockEvent, Assert.Single(old.Pending).Event);
        Assert.DoesNotContain("reportDue", old.Serialize());
    }

    [Fact]
    public void ADamagedFileIsAnErrorNotAFreshStart()
    {
        Assert.Throws<InvalidDataException>(() => OfflineCodes.Parse("{not json"));
        Assert.Empty(OfflineCodes.Parse(null).Codes);
        Assert.Empty(OfflineCodes.Unreadable().Codes);
    }
}

public class PauseOverUnlockTests
{
    private static readonly DateTimeOffset T0 = new(2026, 1, 5, 20, 0, 0, TimeSpan.Zero);

    [Fact]
    public void APauseSetAfterTheUnlockLocksAtOnceAndEndsTheUnlock()
    {
        var e = new LockEnforcer();
        e.ParentUnlocked(T0.AddMinutes(10), T0);
        Assert.False(e.EffectiveLocked(true, T0.AddMinutes(1), pausedAt: null));
        Assert.True(e.EffectiveLocked(true, T0.AddMinutes(2), pausedAt: T0.AddMinutes(1)));
        Assert.Null(e.UnlockUntil);
        // Lifting the pause does not bring the old unlock back.
        Assert.True(e.EffectiveLocked(true, T0.AddMinutes(3), pausedAt: null));
    }

    [Fact]
    public void ThePauseTheUnlockWasForDoesNotOverrideIt()
    {
        // An offline code cannot lift the pause on the hub; the hub's next
        // answer still carries the old pause and must not re-lock the PC.
        var e = new LockEnforcer();
        e.ParentUnlocked(T0.AddMinutes(10), T0);
        Assert.False(e.EffectiveLocked(true, T0.AddMinutes(5), pausedAt: T0.AddMinutes(-30)));
        Assert.NotNull(e.UnlockUntil);
    }

    [Fact]
    public void ThePauseTimeComesFromTheRules()
    {
        var rules = JsonDocument.Parse("{\"paused\":true,\"paused_at\":\"2026-01-05T20:01:00+00:00\"}").RootElement;
        Assert.Equal(T0.AddMinutes(1), GuardEvaluator.PausedAt(rules));
        Assert.Null(GuardEvaluator.PausedAt(JsonDocument.Parse("{\"paused\":true}").RootElement));
    }
}

public class OfflineLimitTests
{
    private static JsonElement Rules(string json) => JsonDocument.Parse(json).RootElement;
    private static readonly DateTimeOffset Noon = new(2026, 1, 5, 12, 0, 0, TimeSpan.Zero); // a Monday

    [Fact]
    public void TheSameDayCountsThisPcsMinutesAgainstWhatWasLeft()
    {
        var snap = new OfflineLimit.Snapshot { Day = "2026-01-05", AllowedMinutes = 120, RemainingMinutes = 30, MeterMs = 3_600_000 };
        var r = OfflineLimit.Remaining(snap, null, "2026-01-05", DayOfWeek.Monday, 3_600_000 + 20 * 60_000);
        Assert.Equal(10, r.RemainingMinutes);
        Assert.Equal(0, OfflineLimit.Remaining(snap, null, "2026-01-05", DayOfWeek.Monday, 3_600_000 + 45 * 60_000).RemainingMinutes);
    }

    [Fact]
    public void TheLimitLocksOfflineInsteadOfFailingOpen()
    {
        var snap = new OfflineLimit.Snapshot { Day = "2026-01-05", AllowedMinutes = 60, RemainingMinutes = 5, MeterMs = 0 };
        var left = OfflineLimit.Remaining(snap, Rules("{}"), "2026-01-05", DayOfWeek.Monday, 10 * 60_000).RemainingMinutes;
        var d = GuardEvaluator.Evaluate(Rules("{}"), Noon, null, left);
        Assert.True(d.Locked);
        Assert.Equal("limit", d.Reason);
        // No cached rules at all: the limit still holds.
        Assert.Equal("limit", GuardEvaluator.Evaluate(null, Noon, null, 0).Reason);
    }

    [Fact]
    public void NoLimitStaysNoLimit()
    {
        var snap = new OfflineLimit.Snapshot { Day = "2026-01-05", AllowedMinutes = null, MeterMs = 0 };
        Assert.Null(OfflineLimit.Remaining(snap, null, "2026-01-05", DayOfWeek.Monday, 999_999_999).RemainingMinutes);
        Assert.False(GuardEvaluator.Evaluate(Rules("{}"), Noon, null, null).Locked);
    }

    [Fact]
    public void ANewDayOfflineUsesTheRulesLimitForThatDay()
    {
        var snap = new OfflineLimit.Snapshot { Day = "2026-01-04", AllowedMinutes = 90, RemainingMinutes = 0, MeterMs = 0 };
        var rules = Rules("{\"daily_limit_min\":60,\"weekend_limit_min\":180,\"day_limits\":{\"wed\":30}}");
        Assert.Equal(new OfflineLimit.Result(60, 40), OfflineLimit.Remaining(snap, rules, "2026-01-05", DayOfWeek.Monday, 20 * 60_000));
        Assert.Equal(180, GuardEvaluator.LimitFor(rules, DayOfWeek.Saturday));
        Assert.Equal(30, GuardEvaluator.LimitFor(rules, DayOfWeek.Wednesday));
        Assert.Null(GuardEvaluator.LimitFor(Rules("{}"), DayOfWeek.Monday));
    }

    [Fact]
    public void BedtimeStillComesBeforeTheLimit()
    {
        var rules = Rules("{\"bedtime_start\":\"11:00\",\"bedtime_end\":\"13:00\"}");
        Assert.Equal("bedtime", GuardEvaluator.Evaluate(rules, Noon, null, 0).Reason);
    }

    [Fact]
    public void TheSnapshotRoundTripsAndADamagedOneIsAnError()
    {
        var snap = new OfflineLimit.Snapshot { Day = "2026-01-05", AllowedMinutes = 60, RemainingMinutes = 12, MeterMs = 42 };
        var back = OfflineLimit.Parse(OfflineLimit.Serialize(snap))!;
        Assert.Equal(12, back.RemainingMinutes);
        Assert.Null(OfflineLimit.Parse(""));
        Assert.Throws<InvalidDataException>(() => OfflineLimit.Parse("{oops"));
    }
}

public class HubReachTests
{
    [Fact]
    public void ARefusedKeyIsAnAuthProblemNotOffline()
    {
        Assert.Equal(HubReach.Auth, HubReach.Classify(401));
        Assert.Equal(HubReach.Auth, HubReach.Classify(403));
        Assert.Equal(HubReach.Offline, HubReach.Classify(null));
        Assert.Equal(HubReach.HubError, HubReach.Classify(502));
        Assert.Equal(HubReach.Hub, HubReach.Classify(200));
    }

    [Fact]
    public void EachProblemSaysWhatItIs()
    {
        Assert.Contains("turned this PC's key down (401)", HubReach.Describe(HubReach.Auth, 401));
        Assert.StartsWith("Offline", HubReach.Describe(HubReach.Offline));
        Assert.Equal("", HubReach.Describe(HubReach.Hub));
        Assert.NotNull(HubReach.RefusalFor(403));
        Assert.Null(HubReach.RefusalFor(422));
    }

    [Fact]
    public void TheTrayShowsTheProblemNotOffline()
    {
        var auth = new GuardState { Source = HubReach.Auth, Problem = HubReach.Describe(HubReach.Auth, 401) };
        Assert.Contains("key down", Assert.Single(TimeText.Notices(auth)));
        Assert.Contains("key down", TimeText.Tooltip(auth));
        Assert.DoesNotContain("Offline", TimeText.Tooltip(auth));
        Assert.Empty(TimeText.Notices(new GuardState { Source = HubReach.Hub }));
    }
}

public class TrayPresenceTests
{
    private static readonly DateTimeOffset T0 = new(2026, 1, 5, 15, 0, 0, TimeSpan.Zero);

    [Fact]
    public void ANewSignInGetsTimeToStartItsTray()
    {
        var p = new TrayPresence();
        Assert.Empty(p.Missing(new uint[] { 2 }, T0));
        Assert.Empty(p.Missing(new uint[] { 2 }, T0 + TrayPresence.StartGrace));
        Assert.Equal(new uint[] { 2 }, p.Missing(new uint[] { 2 }, T0 + TrayPresence.StartGrace + TimeSpan.FromSeconds(1)));
    }

    [Fact]
    public void AClosedTrayIsMissingAndUsageIsUnknown()
    {
        var p = new TrayPresence();
        p.Missing(new uint[] { 2 }, T0);
        p.Heard(2, T0.AddSeconds(30));
        Assert.Empty(p.Missing(new uint[] { 2 }, T0.AddMinutes(2)));
        Assert.Equal(new uint[] { 2 }, p.Missing(new uint[] { 2 }, T0.AddSeconds(30) + TrayPresence.Silence + TimeSpan.FromSeconds(1)));
        var state = new GuardState { UsageUnknown = true };
        Assert.Contains(TimeText.UsageUnknownLine, TimeText.Notices(state));
    }

    [Fact]
    public void NobodySignedInIsNotMissing()
    {
        var p = new TrayPresence();
        p.Missing(new uint[] { 2 }, T0);
        Assert.Empty(p.Missing(Array.Empty<uint>(), T0.AddHours(1)));
        // Signing in again starts a fresh grace.
        Assert.Empty(p.Missing(new uint[] { 2 }, T0.AddHours(1).AddMinutes(1)));
    }
}

public class PipeLineTests
{
    private sealed class SilentStream : Stream
    {
        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => 0; set => throw new NotSupportedException(); }
        public override void Flush() { }
        public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken ct = default)
        {
            await Task.Delay(Timeout.Infinite, ct);
            return 0;
        }
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }

    [Fact]
    public async Task ReadsOneLine()
    {
        var s = new MemoryStream(Encoding.UTF8.GetBytes("{\"cmd\":\"status\"}\r\nrest"));
        Assert.Equal("{\"cmd\":\"status\"}", await PipeProtocol.ReadLineAsync(s, 1024, TimeSpan.FromSeconds(1)));
        Assert.Null(await PipeProtocol.ReadLineAsync(new MemoryStream(), 1024, TimeSpan.FromSeconds(1)));
    }

    [Fact]
    public async Task ALineOverTheCapIsRefused()
    {
        var s = new MemoryStream(Encoding.UTF8.GetBytes(new string('x', 5000) + "\n"));
        await Assert.ThrowsAsync<PipeProtocol.LineTooLongException>(() => PipeProtocol.ReadLineAsync(s, 4096, TimeSpan.FromSeconds(1)));
    }

    [Fact]
    public async Task AClientThatSendsNothingTimesOut()
    {
        await Assert.ThrowsAsync<TimeoutException>(() => PipeProtocol.ReadLineAsync(new SilentStream(), 1024, TimeSpan.FromMilliseconds(100)));
    }

    [Fact]
    public void OnlyASystemOwnedPipeIsTrusted()
    {
        Assert.Null(PipeTrust.Decide(PipeTrust.SystemSid, null, 1234));
        Assert.Null(PipeTrust.Decide(PipeTrust.SystemSid, PipeTrust.SystemSid, 1234));
        var squatter = "S-1-5-21-1000-1000-1000-1001";
        Assert.Contains("held by another program (process 1234)", PipeTrust.Decide(squatter, null, 1234));
        Assert.Contains("running as", PipeTrust.Decide(PipeTrust.SystemSid, squatter, 1234));
        Assert.Contains("Could not check", PipeTrust.Decide(null, null, null));
    }

    [Fact]
    public void UsersMayNotCreateInstancesOrChangeThePipe()
    {
        Assert.Equal(0, (int)(PipeTrust.UserRights & System.IO.Pipes.PipeAccessRights.CreateNewInstance));
        Assert.Equal(0, (int)(PipeTrust.UserRights & System.IO.Pipes.PipeAccessRights.ChangePermissions));
        Assert.Equal(0, (int)(PipeTrust.UserRights & System.IO.Pipes.PipeAccessRights.TakeOwnership));
    }
}

public class ConfigLoadTests
{
    private const string Good = "{\"hubUrl\":\"https://hub.example.test\",\"username\":\"kid\",\"agentKey\":\"wg_fake\",\"deviceKey\":\"windows-test\"}";

    [Fact]
    public void OnlyAMissingFileIsNotSetUp()
    {
        var r = Config.Load("C:/fake/guard.config.json", _ => false, _ => throw new InvalidOperationException("not read"));
        Assert.True(r.Missing);
        Assert.Null(r.Config);
        Assert.StartsWith("No Jarvis Guard configuration", r.Error);
    }

    [Fact]
    public void AccessDeniedIsItsOwnError()
    {
        var r = Config.Load("C:/fake/guard.config.json", _ => true, _ => throw new UnauthorizedAccessException("denied"));
        Assert.False(r.Missing);
        Assert.Contains("Access to C:/fake/guard.config.json was denied", r.Error);
    }

    [Fact]
    public void ADamagedFileSaysWhatIsWrong()
    {
        var bad = Config.Load("C:/fake/guard.config.json", _ => true, _ => "{not json");
        Assert.False(bad.Missing);
        Assert.Contains("not valid JSON", bad.Error);
        var partial = Config.Load("C:/fake/guard.config.json", _ => true, _ => "{\"hubUrl\":\"https://hub.example.test\"}");
        Assert.Contains("it has no username, agentKey, deviceKey", partial.Error);
        Assert.NotNull(Config.Load("C:/fake/guard.config.json", _ => true, _ => Good).Config);
    }
}
