namespace JarvisGuard.Core;

/// <summary>
/// Whether the tray is running in each signed-in child session. The tray is
/// what measures foreground time (the SYSTEM agent cannot see the desktop),
/// and it runs as the child, who can end it in Task Manager. Without this the
/// agent reported <c>trayRunning: true</c> whatever happened, and a closed
/// tray simply counted zero minutes: unlimited time that looked like none.
///
/// The tray says it is alive on every report (every 30 s, even with nothing
/// to report). A session whose tray has not been heard from for
/// <see cref="Silence"/> (after <see cref="StartGrace"/> for a new sign-in)
/// is missing: the day's use is unknown, and the parents are told. Pure.
/// </summary>
public sealed class TrayPresence
{
    public static readonly TimeSpan Silence = TimeSpan.FromMinutes(2);
    public static readonly TimeSpan StartGrace = TimeSpan.FromMinutes(3);

    private readonly Dictionary<uint, DateTimeOffset> _heard = new();
    private readonly Dictionary<uint, DateTimeOffset> _firstSeen = new();

    /// <summary>The tray in <paramref name="sessionId"/> reported.</summary>
    public void Heard(uint sessionId, DateTimeOffset now) => _heard[sessionId] = now;

    /// <summary>
    /// The active child sessions whose tray is missing now.
    /// <paramref name="childSessions"/> are the signed-in sessions usage is
    /// counted for (not session 0, not a parent administrator's).
    /// </summary>
    public IReadOnlyList<uint> Missing(IEnumerable<uint> childSessions, DateTimeOffset now)
    {
        var active = childSessions.ToHashSet();
        foreach (var gone in _firstSeen.Keys.Where(k => !active.Contains(k)).ToList()) _firstSeen.Remove(gone);
        foreach (var gone in _heard.Keys.Where(k => !active.Contains(k)).ToList()) _heard.Remove(gone);

        var missing = new List<uint>();
        foreach (var id in active.OrderBy(x => x))
        {
            if (!_firstSeen.ContainsKey(id)) _firstSeen[id] = now;
            if (_heard.TryGetValue(id, out var last))
            {
                if (now - last > Silence) missing.Add(id);
            }
            else if (now - _firstSeen[id] > StartGrace)
            {
                missing.Add(id);
            }
        }
        return missing;
    }
}
