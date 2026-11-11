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
        };
        if (root.TryGetProperty("rules", out var rules) && rules.ValueKind == JsonValueKind.Object) status.Rules = rules.Clone();
        if (root.TryGetProperty("notices", out var notices) && notices.ValueKind == JsonValueKind.Array) status.Notices = notices.Clone();
        return status;
    }
}
