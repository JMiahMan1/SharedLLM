namespace JarvisGuard.Core;

/// <summary>
/// How the last call to the hub went, in the words the PC shows. A hub that
/// answers 401 or 403 is not "offline": it turned this PC's key down (the key
/// was revoked, the device was removed or reassigned), and nothing gets
/// better by waiting. Saying "offline" sent parents looking at the router.
/// Pure.
/// </summary>
public static class HubReach
{
    /// <summary>GuardState.Source values.</summary>
    public const string Hub = "hub";
    public const string Offline = "offline";
    public const string Auth = "auth";
    public const string HubError = "hub-error";

    /// <summary>
    /// The source for an answer with <paramref name="statusCode"/>; null means
    /// no answer at all (no network, DNS, timeout, refused connection).
    /// </summary>
    public static string Classify(int? statusCode) => statusCode switch
    {
        null => Offline,
        401 or 403 => Auth,
        >= 200 and < 300 => Hub,
        _ => HubError,
    };

    /// <summary>The line the tray and the lock surface show for a source, or "" when all is well.</summary>
    public static string Describe(string? source, int? statusCode = null) => source switch
    {
        Offline => "Offline: these are the last numbers the hub sent. Bedtime, school and the day's limit still apply.",
        Auth => $"The family hub turned this PC's key down{Code(statusCode)}. A parent needs to set Jarvis Guard up again (run install.ps1).",
        HubError => $"The family hub had a problem{Code(statusCode)}. These are the last numbers it sent.",
        _ => "",
    };

    /// <summary>For a log line: why a call did not work.</summary>
    public static string LogReason(int? statusCode) => Classify(statusCode) switch
    {
        Offline => "no answer from the hub (offline)",
        Auth => $"the hub refused this PC's key ({statusCode}): revoked or removed device, reinstall needed",
        HubError => $"the hub answered {statusCode}",
        _ => "ok",
    };

    /// <summary>What a pipe command answers when the hub refused it with <paramref name="statusCode"/>, or null to use its own words.</summary>
    public static string? RefusalFor(int statusCode) => Classify(statusCode) == Auth
        ? $"The family hub turned this PC's key down ({statusCode}). A parent needs to set Jarvis Guard up again."
        : null;

    private static string Code(int? statusCode) => statusCode is { } c ? $" ({c})" : "";
}
