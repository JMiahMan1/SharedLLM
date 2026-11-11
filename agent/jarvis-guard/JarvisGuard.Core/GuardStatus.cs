using System.Text.Json;

namespace JarvisGuard.Core;

/// <summary>
/// What the hub's GET /api/screen-time/{username}/status returns for this PC.
/// Parsed leniently: every field optional, unknown fields ignored.
/// </summary>
public sealed class HubStatus
{
    public bool Locked { get; set; }
    public string? Reason { get; set; }
    public string? Until { get; set; }
    public int RemainingMinutes { get; set; }
    /// <summary>Today's limit; null when there is none.</summary>
    public int? LimitMinutes { get; set; }
    public int UsedMinutes { get; set; }
    public int BonusMinutes { get; set; }
    public int EarnedMinutes { get; set; }
    public int BankedMinutes { get; set; }
    public string? NextLockAt { get; set; }
    public string? NextLockReason { get; set; }
    public string? ThemeId { get; set; }
    public bool WipeRequested { get; set; }
    /// <summary>The hub's raw rules JSON, for offline clock evaluation when the hub is unreachable.</summary>
    public JsonElement? Rules { get; set; }
    public JsonElement? Notices { get; set; }

    public static HubStatus Parse(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        var status = new HubStatus
        {
            Locked = root.TryGetProperty("locked", out var l) && l.ValueKind == JsonValueKind.True,
            Reason = root.TryGetProperty("reason", out var r) && r.ValueKind == JsonValueKind.String ? r.GetString() : null,
            Until = root.TryGetProperty("until", out var u) && u.ValueKind == JsonValueKind.String ? u.GetString() : null,
            RemainingMinutes = root.TryGetProperty("remaining_min", out var rm) && rm.ValueKind == JsonValueKind.Number ? rm.GetInt32() : 0,
            ThemeId = root.TryGetProperty("theme_id", out var t) && t.ValueKind == JsonValueKind.String ? t.GetString() : null,
            WipeRequested = root.TryGetProperty("wipe_requested", out var w) && w.ValueKind == JsonValueKind.True,
            LimitMinutes = root.TryGetProperty("limit_min", out var lm) && lm.ValueKind == JsonValueKind.Number ? lm.GetInt32() : null,
            UsedMinutes = Int(root, "used_min"),
            BonusMinutes = Int(root, "bonus_min"),
            EarnedMinutes = Int(root, "earned_min"),
            BankedMinutes = Int(root, "banked_min"),
            NextLockAt = Str(root, "next_lock_at"),
            NextLockReason = Str(root, "next_lock_reason"),
        };
        if (root.TryGetProperty("rules", out var rules) && rules.ValueKind == JsonValueKind.Object) status.Rules = rules.Clone();
        if (root.TryGetProperty("notices", out var notices) && notices.ValueKind == JsonValueKind.Array) status.Notices = notices.Clone();
        return status;
    }

    private static int Int(JsonElement root, string key) =>
        root.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt32() : 0;

    private static string? Str(JsonElement root, string key) =>
        root.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
}
