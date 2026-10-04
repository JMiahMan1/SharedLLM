/**
 * Human-readable labels for Music Assistant players.
 *
 * Music Assistant keeps whatever name a client registered under, forever. An
 * older build of the web player registered its client name straight from the
 * `jarvis_user` localStorage entry — which is `JSON.stringify(profile)` — so
 * Music Assistant is still holding a player literally named
 *
 *   {"id":1,"username":"default","display_name":"Shared/Default User",…,
 *    "audiobookshelf_api_key":null,"nextcloud_user":"summers",…}'s Web Player (Desktop)
 *
 * Rendering that verbatim dumps 800 characters of somebody's profile (private
 * URLs, email addresses, credential-shaped fields) into the device picker, and
 * `String(...)` on a non-string value would render "[object Object]" just as
 * badly. This module turns any of that into a label a person would recognise.
 */

/** Player labels are UI chrome, not data: long enough for a name, no longer. */
const MAX_LABEL_LENGTH = 60;

/** Trims, collapses internal whitespace and clamps anything too long to read. */
function tidy(value: string): string {
  const collapsed = value.replace(/\s+/g, ' ').trim();
  if (collapsed.length <= MAX_LABEL_LENGTH) return collapsed;
  return `${collapsed.slice(0, MAX_LABEL_LENGTH - 1).trimEnd()}…`;
}

/** A usable label candidate: a non-empty, non-JSON string. */
function usableLabel(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) return null;
  return tidy(trimmed);
}

/**
 * Pulls a person's name out of a serialized profile blob.
 *
 * The blob is only the *prefix* of the registered name -- what follows it (for
 * example "'s Web Player (Desktop)") is ordinary text worth keeping. Rather
 * than guessing where the JSON ends, try every closing brace from the right and
 * keep the longest prefix that actually parses: an apostrophe inside a JSON
 * string value ("Jeremiah's Web Player") would otherwise cut the blob in half.
 */
function nameFromProfileBlob(value: string): { name: string; suffix: string } | null {
  const trimmed = value.trim();
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return null;

  for (let end = trimmed.length; end > 1; end -= 1) {
    const closer = trimmed[end - 1];
    if (closer !== '}' && closer !== ']') continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(trimmed.slice(0, end));
    } catch {
      continue; // Not the end after all -- try an earlier one.
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;

    const profile = parsed as Record<string, unknown>;
    for (const field of ['display_name', 'full_name', 'username'] as const) {
      const candidate = usableLabel(profile[field]);
      if (!candidate) continue;
      // "'s Web Player (Desktop)" -> "Web Player (Desktop)"; the caller puts the
      // possessive back on.
      const suffix = trimmed.slice(end).trim().replace(/^['’]s\s*/, '').trim();
      return { name: candidate, suffix };
    }
    // Valid JSON, but it carries no name: nothing here is worth rendering.
    return null;
  }
  // Truncated or otherwise malformed JSON: not a name we can recover.
  return null;
}

/**
 * The label to show for a Music Assistant player.
 *
 * Tries each candidate in order, salvaging a name out of a serialized profile
 * when that is all there is, and finally falls back to the player id so the
 * device is always selectable.
 */
export function formatMaPlayerName(...candidates: unknown[]): string {
  for (const candidate of candidates) {
    if (typeof candidate === 'string') {
      const fromBlob = nameFromProfileBlob(candidate);
      if (fromBlob) return tidy(fromBlob.suffix ? `${fromBlob.name}'s ${fromBlob.suffix}` : fromBlob.name);
      const label = usableLabel(candidate);
      if (label) return label;
      continue;
    }
    // A structured value (an object that was never stringified upstream) still
    // has a name in it more often than not.
    if (candidate && typeof candidate === 'object') {
      const profile = candidate as Record<string, unknown>;
      for (const field of ['display_name', 'full_name', 'username', 'name'] as const) {
        const label = usableLabel(profile[field]);
        if (label) return label;
      }
    }
  }
  return 'Unknown Player';
}

/** One row of the Media page's device picker. */
export interface MaPlayerListEntry {
  player_id: string;
  name: string;
  available: boolean;
  state: string;
  powered: boolean;
}

/**
 * Normalizes a `players/all` JSON-RPC reply into picker rows.
 *
 * Accepts either the raw array or the `{ result: [...] }` envelope, drops
 * entries with no player id (they cannot be selected anyway), and gives every
 * row a label safe to render. Returns [] for anything that is not a list --
 * including the `{ result: {} }` a disconnected or erroring socket replies with,
 * which used to reach the picker as a crash instead of an empty device list.
 */
export function parseMaPlayerList(raw: unknown): MaPlayerListEntry[] {
  const players =
    raw && typeof raw === 'object' && 'result' in (raw as Record<string, unknown>)
      ? (raw as { result: unknown }).result
      : raw;
  if (!Array.isArray(players)) return [];

  return players
    .map((entry): MaPlayerListEntry => {
      const pl = (entry ?? {}) as Record<string, unknown>;
      return {
        player_id: typeof pl.player_id === 'string' ? pl.player_id : String(pl.player_id ?? ''),
        // `name` is whatever the client registered with Music Assistant, which
        // has included a whole serialized user profile. Never render it raw.
        name: formatMaPlayerName(pl.name, pl.display_name, pl.player_id),
        available: Boolean(pl.available ?? true),
        state: typeof pl.state === 'string' ? pl.state : String(pl.state ?? 'idle'),
        powered: Boolean(pl.powered ?? true),
      };
    })
    .filter((p) => p.player_id !== '');
}