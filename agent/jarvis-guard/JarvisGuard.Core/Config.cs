using System.Text.Json;

namespace JarvisGuard.Core;

/// <summary>
/// Where the agent keeps its files. Everything lives under
/// %ProgramData%\Jarvis Guard. The config (which carries this PC's scoped
/// agent key) is ACL'd to SYSTEM and Administrators only; the public status
/// file the tray reads carries no secrets.
/// </summary>
public static class Config
{
    public static readonly string Root = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Jarvis Guard");

    public static readonly string ConfigPath = Path.Combine(Root, "guard.config.json");
    public static readonly string PublicRoot = Path.Combine(Root, "public");
    // state.json is written by the agent (SYSTEM) and read by the tray and the
    // lock surface (the signed-in child), so it lives one folder down, with
    // Users:Read there. The folder holds nothing but this one file and it is
    // never secret.
    public static readonly string StatePath = Path.Combine(PublicRoot, "state.json");
    public static readonly string LogPath = Path.Combine(Root, "guard.log");
    // The hub's last raw answer, kept agent-side only: the offline fallback
    // re-evaluates these rules against the local clock. May carry the child's
    // household rules; never carries the API key. Admin/SYSTEM-only ACL.
    public static readonly string RawStatusPath = Path.Combine(Root, "status.raw.json");

    public static void WriteRawStatus(string json)
    {
        try { File.WriteAllText(RawStatusPath, json); } catch { /* caching is optional */ }
    }

    public static JsonElement? ReadRawRules()
    {
        try
        {
            if (!File.Exists(RawStatusPath)) return null;
            using var doc = JsonDocument.Parse(File.ReadAllText(RawStatusPath));
            return doc.RootElement.TryGetProperty("rules", out var r) && r.ValueKind == JsonValueKind.Object
                ? r.Clone() : null;
        }
        catch { return null; }
    }

    /// <summary>Named pipe the tray and the lock surface use to ask the SYSTEM agent for things.</summary>
    public const string PipeName = "JarvisGuard";

    public static GuardConfig? Load()
    {
        try
        {
            if (!File.Exists(ConfigPath)) return null;
            return JsonSerializer.Deserialize<GuardConfig>(File.ReadAllText(ConfigPath));
        }
        catch { return null; }
    }

    public static void WriteState(GuardState state)
    {
        Directory.CreateDirectory(PublicRoot);
        var json = JsonSerializer.Serialize(state, JsonOptions.Web);
        var tmp = StatePath + ".tmp";
        File.WriteAllText(tmp, json);
        File.Move(tmp, StatePath, overwrite: true);
    }

    public static GuardState? ReadState()
    {
        try
        {
            if (!File.Exists(StatePath)) return null;
            return JsonSerializer.Deserialize<GuardState>(File.ReadAllText(StatePath), JsonOptions.Web);
        }
        catch { return null; }
    }
}

public sealed class GuardConfig
{
    public string HubUrl { get; set; } = "";
    public string Username { get; set; } = "";
    /// <summary>
    /// This PC's scoped agent key, minted once by the hub for exactly one
    /// registered device. Never grants rule edits, never carries an admin.
    /// </summary>
    public string AgentKey { get; set; } = "";
    public string DeviceKey { get; set; } = "";
    public string Version { get; set; } = "0.1.0";
}

/// <summary>Everything the tray and the lock surface need, and nothing they do not.</summary>
public sealed class GuardState
{
    public bool Locked { get; set; }
    public string? Reason { get; set; }
    public string? Until { get; set; }
    public int RemainingMinutes { get; set; }
    public string ThemeId { get; set; } = "clean-athletic";
    /// <summary>When the agent last heard from the hub (UTC).</summary>
    public string AsOfUtc { get; set; } = "";
    /// <summary>"hub" when the last poll reached the hub, "offline" when running on cached rules.</summary>
    public string Source { get; set; } = "hub";
    public string HubUrl { get; set; } = "";
    public string Username { get; set; } = "";
}

public static class JsonOptions
{
    public static readonly JsonSerializerOptions Web = new(JsonSerializerDefaults.Web);
}
