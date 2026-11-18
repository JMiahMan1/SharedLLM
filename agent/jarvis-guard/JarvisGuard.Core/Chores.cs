using System.Text.Json;

namespace JarvisGuard.Core;

/// <summary>One of today's chores, as the lock surface lists it.</summary>
public sealed class ChoreItem
{
    public string Id { get; set; } = "";
    public string Title { get; set; } = "";
    public bool Completed { get; set; }
}

/// <summary>
/// Today's chores for the lock surface, from the hub's
/// <c>GET /api/integrations/skylight/chores</c> (the same list the Jarvis app
/// shows). Ticking one off is the same <c>.../complete</c> call the app makes;
/// the hub then asks a parent whether it was done right, as on the phone.
/// </summary>
public static class Chores
{
    public readonly record struct Listing(IReadOnlyList<ChoreItem>? Items, string? Error);

    public static Listing Parse(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;
            var status = root.TryGetProperty("status", out var s) && s.ValueKind == JsonValueKind.String ? s.GetString() : null;
            if (!string.Equals(status, "SUCCESS", StringComparison.OrdinalIgnoreCase)
                || !root.TryGetProperty("chores", out var list) || list.ValueKind != JsonValueKind.Array)
            {
                var message = root.TryGetProperty("message", out var m) && m.ValueKind == JsonValueKind.String ? m.GetString() : null;
                return new Listing(null, string.IsNullOrWhiteSpace(message) ? "Chores could not be loaded." : message);
            }
            var items = new List<ChoreItem>();
            foreach (var c in list.EnumerateArray())
            {
                if (c.ValueKind != JsonValueKind.Object || !c.TryGetProperty("id", out var id)) continue;
                var idText = id.ValueKind switch
                {
                    JsonValueKind.String => id.GetString() ?? "",
                    JsonValueKind.Number => id.GetRawText(),
                    _ => "",
                };
                if (idText.Length == 0) continue;
                items.Add(new ChoreItem
                {
                    Id = idText,
                    Title = c.TryGetProperty("title", out var t) && t.ValueKind == JsonValueKind.String ? t.GetString() ?? "Chore" : "Chore",
                    Completed = c.TryGetProperty("completed", out var done) && done.ValueKind == JsonValueKind.True,
                });
            }
            // Still to do first, then the ones already ticked off.
            return new Listing(items.OrderBy(i => i.Completed).ToList(), null);
        }
        catch { return new Listing(null, "Chores could not be loaded."); }
    }
}
