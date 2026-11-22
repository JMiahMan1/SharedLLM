using JarvisGuard.Core;
using Xunit;

namespace JarvisGuard.Tests;

/// <summary>The parents' offline-code verifiers arrive with every status answer, not only with an approval.</summary>
public class OfflineCodesOnStatusTests
{
    private const string Two =
        "[{\"parent\":\"parent-a\",\"salt\":\"00\",\"iterations\":1000,\"hash\":\"aa\"}," +
        "{\"parent\":\"parent-b\",\"salt\":\"11\",\"iterations\":1000,\"hash\":\"bb\"}]";

    [Fact]
    public void AStatusAnswerCarriesTheVerifiers()
    {
        var hub = HubStatus.Parse("{\"locked\":true,\"offline_codes\":" + Two + "}");
        Assert.NotNull(hub.OfflineCodes);
        var codes = new OfflineCodes();
        Assert.True(codes.Remember(hub.OfflineCodes!.Value));
        Assert.Equal(new[] { "parent-a", "parent-b" }, codes.Codes.Select(c => c.Parent));
    }

    [Fact]
    public void AnOlderHubWithoutTheFieldLeavesThemAlone()
    {
        Assert.Null(HubStatus.Parse("{\"locked\":true}").OfflineCodes);
        Assert.Null(HubStatus.Parse("{\"offline_codes\":\"nope\"}").OfflineCodes);
    }

    [Fact]
    public void TheSameSetIsNoChangeAndAChangedOrEmptySetReplacesIt()
    {
        var codes = new OfflineCodes();
        Assert.True(codes.Remember(HubStatus.Parse("{\"offline_codes\":" + Two + "}").OfflineCodes!.Value));
        Assert.False(codes.Remember(HubStatus.Parse("{\"offline_codes\":" + Two + "}").OfflineCodes!.Value));

        var changed = "[{\"parent\":\"parent-a\",\"salt\":\"22\",\"iterations\":1000,\"hash\":\"cc\"}]";
        Assert.True(codes.Remember(HubStatus.Parse("{\"offline_codes\":" + changed + "}").OfflineCodes!.Value));
        Assert.Equal("22", Assert.Single(codes.Codes).Salt);

        Assert.True(codes.Remember(HubStatus.Parse("{\"offline_codes\":[]}").OfflineCodes!.Value));
        Assert.Empty(codes.Codes);
        Assert.StartsWith("No offline code is set up", codes.TryUnlock("246810", DateTimeOffset.Now).Error);
    }
}
