using System.Globalization;
using System.Text.Json;
using JarvisGuard.Core;
using Xunit;

namespace JarvisGuard.Tests;

public class OfflineCodeTests
{
    // Made the way identity makes them: hashlib.pbkdf2_hmac("sha256", code, salt, iterations).hex().
    private const string Salt = "00112233445566778899aabbccddeeff";
    private const string Hash = "86e00dcfbb6bc368ad355e286a3ac9584a9722d4b3d2e760c21db92dcfd5e0f0";
    private static readonly DateTimeOffset T0 = new(2026, 1, 5, 22, 0, 0, TimeSpan.Zero);

    private static OfflineCodes WithCode()
    {
        var codes = new OfflineCodes();
        codes.Remember(JsonDocument.Parse(
            $"[{{\"parent\":\"mom\",\"salt\":\"{Salt}\",\"iterations\":1000,\"hash\":\"{Hash}\"}}]").RootElement);
        return codes;
    }

    [Fact]
    public void MatchesTheVerifierIdentityMakes()
    {
        Assert.True(OfflineCodes.Matches("246810", Salt, 1000, Hash));
        Assert.False(OfflineCodes.Matches("246811", Salt, 1000, Hash));
        Assert.False(OfflineCodes.Matches("246810", Salt, 0, Hash));
        Assert.False(OfflineCodes.Matches("246810", "zz", 1000, Hash));
    }

    [Fact]
    public void AGoodCodeUnlocksAndIsReportedLater()
    {
        var codes = WithCode();
        var r = codes.TryUnlock(" 246810 ", T0);
        Assert.True(r.Ok);
        Assert.True(codes.ReportDue);
        // Kept across a restart of the service.
        Assert.True(OfflineCodes.Parse(codes.Serialize()).ReportDue);
    }

    [Fact]
    public void FiveWrongCodesWaitTenMinutes()
    {
        var codes = WithCode();
        for (var i = 0; i < OfflineCodes.MaxFails; i++) Assert.StartsWith("That code is not right.", codes.TryUnlock("000000", T0).Error);
        Assert.StartsWith("Too many tries", codes.TryUnlock("246810", T0.AddMinutes(1)).Error);
        Assert.True(codes.TryUnlock("246810", T0.AddMinutes(11)).Ok);
    }

    [Fact]
    public void NoCodesYetSaysHowToGetThem()
    {
        Assert.StartsWith("No offline code is set up", new OfflineCodes().TryUnlock("246810", T0).Error);
    }
}

public class ChoresTests
{
    [Fact]
    public void ListsTodaysChoresStillToDoFirst()
    {
        var listing = Chores.Parse("{\"status\":\"SUCCESS\",\"chores\":[" +
            "{\"id\":\"a1\",\"title\":\"Feed the cat\",\"completed\":true}," +
            "{\"id\":7,\"title\":\"Make the bed\",\"completed\":false}," +
            "{\"title\":\"no id\"}]}");
        Assert.Null(listing.Error);
        Assert.Equal(new[] { "7", "a1" }, listing.Items!.Select(c => c.Id));
        Assert.False(listing.Items![0].Completed);
    }

    [Fact]
    public void AFailureCarriesTheHubsWords()
    {
        var listing = Chores.Parse("{\"status\":\"FAILURE\",\"message\":\"Skylight is disabled for your account\"}");
        Assert.Null(listing.Items);
        Assert.Equal("Skylight is disabled for your account", listing.Error);
        Assert.Equal("Chores could not be loaded.", Chores.Parse("<html>").Error);
    }
}

public class LockTextTests
{
    [Fact]
    public void SaysWhyCalmly()
    {
        Assert.Equal("It's bedtime", LockText.Title("bedtime"));
        Assert.Equal("Chores first", LockText.Title("chores"));
        Assert.Equal("This PC is locked", LockText.Title(null));
    }

    [Fact]
    public void SaysWhenItLifts()
    {
        Assert.Equal("Tick off today's chores and it unlocks.", LockText.Until("chores", null));
        Assert.Equal("Until a parent lifts it.", LockText.Until("paused", null));
        var local = new DateTimeOffset(2026, 1, 6, 7, 0, 0, TimeZoneInfo.Local.GetUtcOffset(new DateTime(2026, 1, 6, 7, 0, 0)));
        Assert.Equal($"Until {local.ToString("t", CultureInfo.InvariantCulture)}.", LockText.Until("bedtime", local.ToString("O"), CultureInfo.InvariantCulture));
        var midnight = new DateTimeOffset(2026, 1, 6, 0, 0, 0, TimeZoneInfo.Local.GetUtcOffset(new DateTime(2026, 1, 6)));
        Assert.Equal("Until midnight.", LockText.Until("limit", midnight.ToString("O")));
    }

    [Fact]
    public void CountsDownToTheSignOut()
    {
        var now = new DateTimeOffset(2026, 1, 5, 21, 0, 0, TimeSpan.Zero);
        Assert.Equal("Signing out in 1:45. Save your work.", LockText.Countdown(now.AddSeconds(105).ToString("O"), now));
        Assert.Equal("Signing out now.", LockText.Countdown(now.AddSeconds(-1).ToString("O"), now));
        Assert.Equal("", LockText.Countdown(null, now));
    }
}
