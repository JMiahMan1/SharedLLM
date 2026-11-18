using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace JarvisGuard.Core;

/// <summary>
/// A parent's offline unlock code, checked on the PC with no connection: the
/// same scheme as the phone (services/ui/android OfflineCode.java and
/// GuardApi.offlineUnlock). The PC holds only PBKDF2-SHA256 verifiers (salt,
/// iterations, hash) that arrived inside a real online parent unlock; the
/// code itself never leaves the parent. Five wrong codes wait ten minutes. A
/// good code lifts the lock for ten minutes and the parents are told
/// (<c>offline_unlock</c>) as soon as the hub answers again.
/// </summary>
public sealed class OfflineCodes
{
    public const int MaxFails = 5;
    public static readonly TimeSpan FailWindow = TimeSpan.FromMinutes(10);
    public static readonly TimeSpan UnlockFor = TimeSpan.FromMinutes(10);

    public sealed class Verifier
    {
        public string Parent { get; set; } = "";
        public string Salt { get; set; } = "";
        public int Iterations { get; set; }
        public string Hash { get; set; } = "";
    }

    public List<Verifier> Codes { get; set; } = new();
    public List<DateTimeOffset> Fails { get; set; } = new();
    /// <summary>An offline unlock happened and the hub has not been told yet.</summary>
    public bool ReportDue { get; set; }

    public readonly record struct Result(bool Ok, string? Error);

    /// <summary>Keep the verifiers an online parent unlock returned (<c>offline_codes</c>).</summary>
    public void Remember(JsonElement offlineCodes)
    {
        if (offlineCodes.ValueKind != JsonValueKind.Array) return;
        var codes = new List<Verifier>();
        foreach (var c in offlineCodes.EnumerateArray())
        {
            if (c.ValueKind != JsonValueKind.Object) continue;
            codes.Add(new Verifier
            {
                Parent = Str(c, "parent"),
                Salt = Str(c, "salt"),
                Iterations = c.TryGetProperty("iterations", out var i) && i.ValueKind == JsonValueKind.Number ? i.GetInt32() : 0,
                Hash = Str(c, "hash"),
            });
        }
        Codes = codes;
    }

    public Result TryUnlock(string? code, DateTimeOffset now)
    {
        Fails = Fails.Where(t => now - t < FailWindow).ToList();
        if (Fails.Count >= MaxFails) return new Result(false, "Too many tries. Wait ten minutes, then try again.");
        if (Codes.Count == 0)
            return new Result(false, "No offline code is set up on this PC yet. A parent signs in here once while it is online to set it up.");
        var typed = (code ?? "").Trim();
        if (Codes.Any(c => Matches(typed, c.Salt, c.Iterations, c.Hash)))
        {
            Fails.Clear();
            ReportDue = true;
            return new Result(true, null);
        }
        Fails.Add(now);
        return new Result(false, "That code is not right.");
    }

    /// <summary>True when <paramref name="code"/> matches the verifier. Malformed verifiers never match.</summary>
    public static bool Matches(string code, string saltHex, int iterations, string hashHex)
    {
        if (string.IsNullOrEmpty(code) || iterations < 1 || iterations > 2_000_000) return false;
        try
        {
            var expected = Convert.FromHexString(hashHex);
            if (expected.Length == 0) return false;
            var actual = Rfc2898DeriveBytes.Pbkdf2(Encoding.UTF8.GetBytes(code), Convert.FromHexString(saltHex),
                iterations, HashAlgorithmName.SHA256, expected.Length);
            return CryptographicOperations.FixedTimeEquals(expected, actual);
        }
        catch { return false; }
    }

    public string Serialize() => JsonSerializer.Serialize(this, JsonOptions.Web);

    public static OfflineCodes Parse(string? json)
    {
        try { return string.IsNullOrWhiteSpace(json) ? new OfflineCodes() : JsonSerializer.Deserialize<OfflineCodes>(json, JsonOptions.Web) ?? new OfflineCodes(); }
        catch { return new OfflineCodes(); }
    }

    private static string Str(JsonElement e, string key) =>
        e.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : "";
}
