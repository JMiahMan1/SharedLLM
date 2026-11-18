namespace JarvisGuard.Core;

/// <summary>
/// Decides, on every tick, which signed-in sessions to sign out. Pure, so it
/// can be tested off Windows.
///
/// The lock is a session sign-out, but not at once: the child first gets the
/// lock surface for <see cref="FirstGrace"/>, so they can see why, ask for
/// more time or fetch a parent, the same things the phone's lock screen
/// offers. Signing back in while the lock still holds gets the lock surface
/// again, for the shorter <see cref="ReturnGrace"/>, then another sign-out.
/// The old code signed out once per lock and never again, so a child who
/// signed straight back in kept the PC for the rest of the lock.
///
/// A parent's sign-in on the lock surface lifts the lock on this PC for the
/// time the hub granted (<see cref="UnlockUntil"/>), whatever the hub's
/// status says meanwhile: the hub keeps reporting bedtime or the day's limit,
/// and the old code signed the session out again on the next poll.
/// </summary>
public sealed class LockEnforcer
{
    public static readonly TimeSpan DefaultFirstGrace = TimeSpan.FromMinutes(2);
    public static readonly TimeSpan DefaultReturnGrace = TimeSpan.FromSeconds(45);

    private readonly Dictionary<uint, DateTimeOffset> _seenLocked = new();
    private bool _signedOutThisLock;

    public LockEnforcer(TimeSpan? firstGrace = null, TimeSpan? returnGrace = null)
    {
        FirstGrace = firstGrace ?? DefaultFirstGrace;
        ReturnGrace = returnGrace ?? DefaultReturnGrace;
    }

    public TimeSpan FirstGrace { get; }
    public TimeSpan ReturnGrace { get; }

    /// <summary>Until when a parent's unlock on this PC holds (null: none).</summary>
    public DateTimeOffset? UnlockUntil { get; private set; }

    /// <summary>A parent signed in on the lock surface; the hub said until when.</summary>
    public void ParentUnlocked(DateTimeOffset until)
    {
        UnlockUntil = until;
        _seenLocked.Clear();
        _signedOutThisLock = false;
    }

    public bool ParentUnlockActive(DateTimeOffset now) => UnlockUntil is { } u && now < u;

    /// <summary>Whether this PC is locked now: the hub's (or the offline) verdict, less a parent's unlock.</summary>
    public bool EffectiveLocked(bool locked, DateTimeOffset now) => locked && !ParentUnlockActive(now);

    /// <summary>
    /// The sessions to sign out now. <paramref name="sessions"/> are the
    /// active sessions that may be signed out (the children's, not a parent
    /// administrator's own).
    /// </summary>
    public IReadOnlyList<uint> SessionsToSignOut(bool effectiveLocked, IEnumerable<uint> sessions, DateTimeOffset now)
    {
        if (!effectiveLocked)
        {
            _seenLocked.Clear();
            _signedOutThisLock = false;
            return Array.Empty<uint>();
        }
        var active = sessions.ToHashSet();
        foreach (var gone in _seenLocked.Keys.Where(k => !active.Contains(k)).ToList()) _seenLocked.Remove(gone);

        var result = new List<uint>();
        foreach (var id in active)
        {
            if (!_seenLocked.ContainsKey(id)) _seenLocked[id] = now;
            if (now - _seenLocked[id] >= Grace) result.Add(id);
        }
        if (result.Count > 0)
        {
            _signedOutThisLock = true;
            foreach (var id in result) _seenLocked.Remove(id);
        }
        return result;
    }

    private TimeSpan Grace => _signedOutThisLock ? ReturnGrace : FirstGrace;

    /// <summary>When the earliest session still being shown the lock surface will be signed out (for its countdown).</summary>
    public DateTimeOffset? NextSignOutAt(bool effectiveLocked)
    {
        if (!effectiveLocked || _seenLocked.Count == 0) return null;
        return _seenLocked.Values.Min() + Grace;
    }
}
