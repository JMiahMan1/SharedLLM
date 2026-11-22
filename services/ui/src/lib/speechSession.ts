/**
 * Who owns the phone's media controls right now: the music player, or a
 * read-aloud (an imported study section being spoken).
 *
 * Both use the same Android media notification. While a read-aloud holds it,
 * the lock-screen Play button must resume the reading, not the last song, so
 * the music player's action listener checks this before acting.
 */
let owner: string | null = null;

export function claimSpeechSession(id: string): void {
  owner = id;
}

export function releaseSpeechSession(id: string): void {
  if (owner === id) owner = null;
}

export function speechSessionActive(): boolean {
  return owner !== null;
}
