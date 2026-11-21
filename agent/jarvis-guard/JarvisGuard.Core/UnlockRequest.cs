using System.Text.Json;

namespace JarvisGuard.Core;

/// <summary>
/// Asking a parent to unlock this PC, decided on the parent's phone. The lock
/// surface asks (pipe <c>unlock-request</c>), the SYSTEM agent sends it to the
/// hub with this PC's device key (<c>POST /api/guard/unlock-requests</c>),
/// both parents get Approve for N minutes / Deny in their Jarvis app, and the
/// agent polls its own request (<c>GET /api/guard/unlock-requests/{id}</c>,
/// which the hub answers only for this PC). The PC unlocks only on an approval
/// read from the hub: <c>until</c> and <c>decided_at</c> are the hub's, never
/// computed here. No password is ever typed on the PC.
///
/// The hub's rules: a request waits ten minutes, a newer one supersedes it, a
/// parent's answer is final, and a device may ask once every 30 s and ten
/// times an hour. Pure, so it is tested off Windows.
/// </summary>
public static class UnlockRequest
{
    /// <summary>The lengths the lock surface offers.</summary>
    public static readonly int[] MinuteChoices = { 15, 30, 60 };
    public const int MinMinutes = 5;
    public const int MaxMinutes = 240;
    /// <summary>How often the agent asks the hub about a request still waiting.</summary>
    public static readonly TimeSpan PollEvery = TimeSpan.FromSeconds(5);
    /// <summary>How long past its expiry the agent stops asking, if the hub never said.</summary>
    public static readonly TimeSpan GiveUpAfterExpiry = TimeSpan.FromMinutes(2);

    /// <summary>Pipe <c>UnlockStatus</c> values.</summary>
    public const string None = "none";
    public const string Pending = "pending";
    public const string Approved = "approved";
    public const string Denied = "denied";
    public const string Expired = "expired";
    public const string Superseded = "superseded";

    /// <summary>The hub's answer about one request. <see cref="OfflineCodes"/> is the raw verifier list (approvals only).</summary>
    public sealed record Answer(string Status, DateTimeOffset? Until, DateTimeOffset? DecidedAt, string? DecidedBy, int? MinutesGranted, JsonElement? OfflineCodes);

    /// <summary>The new request's id and expiry from the hub's answer to the ask. Throws <see cref="InvalidDataException"/> when it is not one.</summary>
    public static (long Id, DateTimeOffset ExpiresAt) ParseCreated(string json)
    {
        var request = RequestOf(json);
        var id = Id(request);
        var expires = Time(request, "expires_at") ?? throw new InvalidDataException("The hub's answer has no expiry.");
        return (id, expires);
    }

    /// <summary>
    /// The hub's answer about request <paramref name="askedId"/>. Throws
    /// <see cref="InvalidDataException"/> when it is unreadable, about another
    /// request, or an approval this PC cannot trust (no time it was given, an
    /// end before it, or longer than <see cref="MaxMinutes"/>): such an answer
    /// never unlocks.
    /// </summary>
    public static Answer ParseAnswer(string json, long askedId)
    {
        JsonElement root;
        try { root = JsonDocument.Parse(json).RootElement; }
        catch (JsonException ex) { throw new InvalidDataException($"The hub's answer is not JSON: {ex.Message}"); }
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("request", out var request) || request.ValueKind != JsonValueKind.Object)
            throw new InvalidDataException("The hub's answer has no request.");
        if (Id(request) != askedId) throw new InvalidDataException("The hub answered about another request.");
        var status = request.TryGetProperty("status", out var s) && s.ValueKind == JsonValueKind.String ? s.GetString() ?? "" : "";
        var decidedBy = request.TryGetProperty("decided_by", out var by) && by.ValueKind == JsonValueKind.String ? by.GetString() : null;
        switch (status)
        {
            case Pending or Denied or Expired or Superseded:
                return new Answer(status, null, null, decidedBy, null, null);
            case Approved:
                var decidedAt = Time(request, "decided_at") ?? throw new InvalidDataException("The approval has no time it was given.");
                var until = Time(request, "until") ?? throw new InvalidDataException("The approval has no end.");
                if (until <= decidedAt) throw new InvalidDataException("The approval ends before it was given.");
                if (until - decidedAt > TimeSpan.FromMinutes(MaxMinutes) + TimeSpan.FromMinutes(1))
                    throw new InvalidDataException($"The approval is longer than {MaxMinutes} minutes.");
                int? minutes = request.TryGetProperty("minutes_granted", out var m) && m.ValueKind == JsonValueKind.Number ? m.GetInt32() : null;
                JsonElement? codes = root.TryGetProperty("offline_codes", out var c) && c.ValueKind == JsonValueKind.Array ? c.Clone() : null;
                return new Answer(Approved, until, decidedAt, decidedBy, minutes, codes);
            default:
                throw new InvalidDataException($"The hub answered an unknown status '{status}'.");
        }
    }

    /// <summary>What the lock surface says about a request in <paramref name="status"/>.</summary>
    public static string Describe(string status, string? decidedBy = null) => status switch
    {
        Pending => "Asked. Waiting for a parent to answer on their phone...",
        Approved => "A parent said yes. Unlocking...",
        Denied => $"{(string.IsNullOrWhiteSpace(decidedBy) ? "A parent" : decidedBy)} said no this time.",
        Expired => "Nobody answered in ten minutes. You can ask again.",
        Superseded => "A newer request replaced this one.",
        _ => "",
    };

    /// <summary>Whether to keep asking the hub about a request that expires at <paramref name="expiresAt"/>.</summary>
    public static bool StillWorthAsking(DateTimeOffset expiresAt, DateTimeOffset now) => now < expiresAt + GiveUpAfterExpiry;

    private static JsonElement RequestOf(string json)
    {
        JsonElement root;
        try { root = JsonDocument.Parse(json).RootElement; }
        catch (JsonException ex) { throw new InvalidDataException($"The hub's answer is not JSON: {ex.Message}"); }
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("request", out var request) || request.ValueKind != JsonValueKind.Object)
            throw new InvalidDataException("The hub's answer has no request.");
        return request;
    }

    private static long Id(JsonElement request) =>
        request.TryGetProperty("id", out var id) && id.ValueKind == JsonValueKind.Number && id.TryGetInt64(out var v)
            ? v
            : throw new InvalidDataException("The hub's answer has no request id.");

    private static DateTimeOffset? Time(JsonElement request, string name) =>
        request.TryGetProperty(name, out var t) && t.ValueKind == JsonValueKind.String
        && DateTimeOffset.TryParse(t.GetString(), System.Globalization.CultureInfo.InvariantCulture, System.Globalization.DateTimeStyles.RoundtripKind, out var v)
            ? v
            : null;
}
