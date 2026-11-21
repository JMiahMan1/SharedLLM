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
    // Today's running total of foreground minutes (agent-side only), so a
    // restart mid-day carries on from it instead of starting at zero.
    public static readonly string UsagePath = Path.Combine(Root, "usage.today.json");
    // The parents' offline-code verifiers (salt, iterations, hash; never a code)
    // and the recent wrong tries. Agent-side only: SYSTEM/Administrators ACL.
    public static readonly string OfflineCodesPath = Path.Combine(Root, "offline.codes.json");
    // The day's limit as the hub last answered it, next to this PC's own count
    // at that moment (OfflineLimit.Snapshot), so the limit still holds offline
    // and across a restart. Agent-side only.
    public static readonly string LimitSnapshotPath = Path.Combine(Root, "limit.snapshot.json");

    public static void WriteRawStatus(string json)
    {
        try { File.WriteAllText(RawStatusPath, json); } catch { /* caching is optional */ }
    }

    /// <summary>The cached rules and the granted_until the hub answered with them (for the offline evaluator).</summary>
    public static (JsonElement? Rules, DateTimeOffset? GrantedUntil) ReadRawCache()
    {
        try
        {
            if (!File.Exists(RawStatusPath)) return (null, null);
            return ParseRawCache(File.ReadAllText(RawStatusPath));
        }
        catch { return (null, null); }
    }

    public static (JsonElement? Rules, DateTimeOffset? GrantedUntil) ParseRawCache(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;
            JsonElement? rules = root.TryGetProperty("rules", out var r) && r.ValueKind == JsonValueKind.Object ? r.Clone() : null;
            DateTimeOffset? granted = root.TryGetProperty("granted_until", out var g) && g.ValueKind == JsonValueKind.String
                && DateTimeOffset.TryParse(g.GetString(), out var parsed) ? parsed : null;
            return (rules, granted);
        }
        catch { return (null, null); }
    }

    /// <summary>Named pipe the tray and the lock surface use to ask the SYSTEM agent for things.</summary>
    public const string PipeName = "JarvisGuard";

    /// <summary>
    /// What loading the config found. Only a missing file means "not set up";
    /// a file that cannot be read (access denied, locked) or that does not
    /// hold a whole config is its own error, with the reason, so a damaged or
    /// mis-permissioned install is never mistaken for an uninstalled one.
    /// </summary>
    public sealed record ConfigLoad(GuardConfig? Config, bool Missing, string? Error)
    {
        public static ConfigLoad Ok(GuardConfig c) => new(c, false, null);
        public static ConfigLoad NotSetUp(string path) =>
            new(null, true, $"No Jarvis Guard configuration at {path}. Run install.ps1 as an administrator.");
        public static ConfigLoad Broken(string error) => new(null, false, error);
    }

    public static ConfigLoad Load() => Load(ConfigPath, File.Exists, File.ReadAllText);

    /// <summary>Testable core of <see cref="Load()"/>: the file system is passed in.</summary>
    public static ConfigLoad Load(string path, Func<string, bool> exists, Func<string, string> read)
    {
        if (!exists(path)) return ConfigLoad.NotSetUp(path);
        string text;
        try { text = read(path); }
        catch (UnauthorizedAccessException ex)
        {
            return ConfigLoad.Broken($"Access to {path} was denied ({ex.Message}). The agent must run as SYSTEM; run install.ps1 again as an administrator to repair the folder's permissions.");
        }
        catch (FileNotFoundException)
        {
            return ConfigLoad.NotSetUp(path);
        }
        catch (IOException ex)
        {
            return ConfigLoad.Broken($"{path} could not be read: {ex.Message}");
        }
        var (config, error) = TryParseConfig(text);
        return config is not null ? ConfigLoad.Ok(config) : ConfigLoad.Broken($"{path} is not a usable configuration: {error} Run install.ps1 again as an administrator.");
    }

    /// <summary>
    /// install.ps1 writes camelCase keys (hubUrl, agentKey); the properties are
    /// PascalCase. Case-sensitive parsing left every field empty, so the agent
    /// started with no hub, no user and no key.
    /// </summary>
    public static GuardConfig? ParseConfig(string json) => TryParseConfig(json).Config;

    /// <summary>The config, or why the text is not one.</summary>
    public static (GuardConfig? Config, string? Error) TryParseConfig(string json)
    {
        GuardConfig? config;
        try { config = JsonSerializer.Deserialize<GuardConfig>(json.TrimStart('\uFEFF'), JsonOptions.Web); }
        catch (JsonException ex) { return (null, $"it is not valid JSON ({ex.Message})."); }
        if (config is null) return (null, "it is empty.");
        var missing = new List<string>();
        if (string.IsNullOrWhiteSpace(config.HubUrl)) missing.Add("hubUrl");
        if (string.IsNullOrWhiteSpace(config.Username)) missing.Add("username");
        if (string.IsNullOrWhiteSpace(config.AgentKey)) missing.Add("agentKey");
        if (string.IsNullOrWhiteSpace(config.DeviceKey)) missing.Add("deviceKey");
        return missing.Count > 0 ? (null, $"it has no {string.Join(", ", missing)}.") : (config, null);
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
    /// <summary>Used today, counting this PC's minutes not yet sent to the hub.</summary>
    public int UsedMinutes { get; set; }
    /// <summary>Everything allowed today (limit + bonus + earned + banked); null with no limit.</summary>
    public int? AllowedMinutes { get; set; }
    public int BonusMinutes { get; set; }
    public int EarnedMinutes { get; set; }
    public int BankedMinutes { get; set; }
    /// <summary>When the next lock lands (ISO), and why: what the 10/5/1-minute warnings count down to.</summary>
    public string? NextLockAt { get; set; }
    public string? NextLockReason { get; set; }
    public string ThemeId { get; set; } = "clean-athletic";
    /// <summary>While locked: when this PC signs the session out (ISO), for the lock surface's countdown.</summary>
    public string? SignOutAt { get; set; }
    /// <summary>A parent unlocked this PC on its lock surface until then (ISO); the hub still says locked.</summary>
    public string? ParentUnlockUntil { get; set; }
    /// <summary>When the agent last heard from the hub (UTC).</summary>
    public string AsOfUtc { get; set; } = "";
    /// <summary>
    /// <see cref="HubReach"/>: "hub" when the last poll reached the hub;
    /// "offline" (no answer), "auth" (the hub refused this PC's key) or
    /// "hub-error" when running on cached rules; "config-error" when the agent
    /// could not load its configuration and is not guarding at all.
    /// </summary>
    public string Source { get; set; } = "hub";
    /// <summary>What is wrong, in words for the screen, when <see cref="Source"/> is not "hub"; else null.</summary>
    public string? Problem { get; set; }
    /// <summary>
    /// A signed-in child session has no tray, so this PC's minutes are not
    /// being counted: the used and remaining figures are the last known ones,
    /// not today's truth. The parents are told.
    /// </summary>
    public bool UsageUnknown { get; set; }
    /// <summary>When the parents' current pause was set (ISO), from the rules' paused_at; null with none.</summary>
    public string? PausedAt { get; set; }
    public string HubUrl { get; set; } = "";
    public string Username { get; set; } = "";
}

public static class JsonOptions
{
    public static readonly JsonSerializerOptions Web = new(JsonSerializerDefaults.Web);
}
