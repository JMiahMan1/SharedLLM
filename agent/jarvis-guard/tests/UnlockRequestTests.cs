using JarvisGuard.Core;
using Xunit;

namespace JarvisGuard.Tests;

/// <summary>The PC unlocks only on the hub's approval of its own request, with the hub's times.</summary>
public class UnlockRequestTests
{
    private const string Decided = "2026-01-05T20:00:00.123456+00:00";
    private static readonly DateTimeOffset T0 = DateTimeOffset.Parse(Decided);

    private static string Answer(string request, string extra = "") =>
        $"{{\"request\":{{{request}}}{(extra.Length > 0 ? "," + extra : "")}}}";

    [Fact]
    public void ANewRequestGivesItsIdAndExpiry()
    {
        var (id, expires) = UnlockRequest.ParseCreated(Answer("\"id\":12,\"status\":\"pending\",\"expires_at\":\"2026-01-05T20:10:00+00:00\""));
        Assert.Equal(12, id);
        Assert.Equal(DateTimeOffset.Parse("2026-01-05T20:10:00+00:00"), expires);
    }

    [Theory]
    [InlineData("not json")]
    [InlineData("{}")]
    [InlineData("{\"request\":{\"status\":\"pending\",\"expires_at\":\"2026-01-05T20:10:00+00:00\"}}")]
    [InlineData("{\"request\":{\"id\":12,\"status\":\"pending\"}}")]
    public void AnUnreadableAskAnswerIsAnError(string json)
    {
        Assert.Throws<InvalidDataException>(() => UnlockRequest.ParseCreated(json));
    }

    [Fact]
    public void AnApprovalCarriesTheHubsTimesAndTheOfflineVerifiers()
    {
        var a = UnlockRequest.ParseAnswer(Answer(
            $"\"id\":12,\"status\":\"approved\",\"decided_by\":\"parent-a\",\"decided_at\":\"{Decided}\",\"until\":\"2026-01-05T20:30:00.123456+00:00\",\"minutes_granted\":30",
            "\"offline_codes\":[{\"parent\":\"parent-a\",\"salt\":\"00\",\"iterations\":1000,\"hash\":\"aa\"}]"), 12);
        Assert.Equal(UnlockRequest.Approved, a.Status);
        Assert.Equal(T0, a.DecidedAt);
        Assert.Equal(T0.AddMinutes(30), a.Until);
        Assert.Equal(30, a.MinutesGranted);
        Assert.Equal("parent-a", a.DecidedBy);
        Assert.NotNull(a.OfflineCodes);
        var codes = new OfflineCodes();
        codes.Remember(a.OfflineCodes!.Value);
        Assert.Equal("parent-a", Assert.Single(codes.Codes).Parent);
    }

    [Theory]
    [InlineData("pending")]
    [InlineData("denied")]
    [InlineData("expired")]
    [InlineData("superseded")]
    public void EveryOtherAnswerNeverUnlocks(string status)
    {
        // Even if a stray until came along, only an approval has one.
        var a = UnlockRequest.ParseAnswer(Answer($"\"id\":12,\"status\":\"{status}\",\"until\":\"2026-01-05T21:00:00+00:00\""), 12);
        Assert.Equal(status, a.Status);
        Assert.Null(a.Until);
        Assert.Null(a.OfflineCodes);
    }

    [Fact]
    public void AnAnswerAboutAnotherRequestIsRefused()
    {
        var json = Answer($"\"id\":13,\"status\":\"approved\",\"decided_at\":\"{Decided}\",\"until\":\"2026-01-05T20:30:00+00:00\"");
        Assert.Throws<InvalidDataException>(() => UnlockRequest.ParseAnswer(json, 12));
    }

    [Theory]
    [InlineData("\"until\":\"2026-01-05T20:30:00+00:00\"")]                                             // no time it was given
    [InlineData("\"decided_at\":\"2026-01-05T20:00:00+00:00\"")]                                        // no end
    [InlineData("\"decided_at\":\"2026-01-05T20:00:00+00:00\",\"until\":\"2026-01-05T19:59:00+00:00\"")] // ends before it began
    [InlineData("\"decided_at\":\"2026-01-05T20:00:00+00:00\",\"until\":\"2026-01-06T20:00:00+00:00\"")] // a day, past the most a parent can give
    public void AnApprovalThePcCannotTrustNeverUnlocks(string times)
    {
        Assert.Throws<InvalidDataException>(() => UnlockRequest.ParseAnswer(Answer($"\"id\":12,\"status\":\"approved\",{times}"), 12));
    }

    [Fact]
    public void AnUnknownStatusIsAnErrorNotAGuess()
    {
        Assert.Throws<InvalidDataException>(() => UnlockRequest.ParseAnswer(Answer("\"id\":12,\"status\":\"maybe\""), 12));
        Assert.Throws<InvalidDataException>(() => UnlockRequest.ParseAnswer("{\"detail\":\"No such request\"}", 12));
    }

    [Fact]
    public void TheApprovalStartsAtTheHubsTimeSoALaterPauseStillWins()
    {
        var a = UnlockRequest.ParseAnswer(Answer(
            $"\"id\":12,\"status\":\"approved\",\"decided_at\":\"{Decided}\",\"until\":\"2026-01-05T20:30:00+00:00\""), 12);
        var e = new LockEnforcer();
        e.ParentUnlocked(a.Until!.Value, a.DecidedAt);
        // The pause the approval lifted (before it) does not re-lock ...
        Assert.False(e.EffectiveLocked(true, T0.AddMinutes(1), pausedAt: T0.AddMinutes(-20)));
        // ... a pause a parent gives after it does.
        Assert.True(e.EffectiveLocked(true, T0.AddMinutes(3), pausedAt: T0.AddMinutes(2)));
    }

    [Fact]
    public void TheAgentStopsAskingAWhileAfterTheRequestExpired()
    {
        var expires = T0.AddMinutes(10);
        Assert.True(UnlockRequest.StillWorthAsking(expires, T0.AddMinutes(11)));
        Assert.False(UnlockRequest.StillWorthAsking(expires, expires + UnlockRequest.GiveUpAfterExpiry));
    }

    [Fact]
    public void TheLockSurfaceSaysWhoSaidNo()
    {
        Assert.Equal("parent-a said no this time.", UnlockRequest.Describe(UnlockRequest.Denied, "parent-a"));
        Assert.Equal("A parent said no this time.", UnlockRequest.Describe(UnlockRequest.Denied));
        Assert.Contains("ask again", UnlockRequest.Describe(UnlockRequest.Expired));
        Assert.All(UnlockRequest.MinuteChoices, m => Assert.InRange(m, UnlockRequest.MinMinutes, UnlockRequest.MaxMinutes));
    }

    [Fact]
    public void ThePipeCarriesNoPasswordFields()
    {
        var names = typeof(PipeProtocol.Request).GetProperties().Select(p => p.Name).ToList();
        Assert.DoesNotContain(names, n => n.Contains("Password", StringComparison.OrdinalIgnoreCase));
        Assert.DoesNotContain(names, n => n.StartsWith("Parent", StringComparison.Ordinal));
    }
}
