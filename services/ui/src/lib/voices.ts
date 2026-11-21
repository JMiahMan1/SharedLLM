/**
 * Voice names people can read. Kokoro voices are ids like `af_heart` (the
 * first letter is the accent, the second female or male), blends are ids
 * joined by commas, and Piper voices are `piper:<locale>-<name>-<quality>`.
 */

const ACCENTS: Record<string, string> = {
  a: 'American',
  b: 'British',
  e: 'Spanish',
  f: 'French',
  h: 'Hindi',
  i: 'Italian',
  j: 'Japanese',
  p: 'Brazilian',
  z: 'Mandarin',
};

const LOCALES: Record<string, string> = {
  en_US: 'American',
  en_GB: 'British',
};

function title(word: string): string {
  return word ? word[0].toUpperCase() + word.slice(1) : word;
}

export interface VoiceInfo {
  id: string;
  name: string;
  /** For grouping in a list: "American", "British", "Blends", "Piper". */
  group: string;
}

/** A Kokoro id such as `af_heart` as {name: "Heart, American female", group: "American"}. */
function kokoro(id: string): VoiceInfo | null {
  const match = /^([a-z])([fm])_([a-z0-9]+)$/.exec(id);
  if (!match) return null;
  const accent = ACCENTS[match[1]] ?? 'Other';
  const who = match[2] === 'f' ? 'female' : 'male';
  return { id, name: `${title(match[3])}, ${accent} ${who}`, group: accent };
}

/** `piper:en_US-hfc_male-medium` as {name: "HFC male, American (Piper)", group: "Piper"}. */
function piper(id: string): VoiceInfo | null {
  const match = /^piper:([a-z]{2}_[A-Z]{2})-(.+?)(?:-(x_low|low|medium|high))?$/.exec(id);
  if (!match) return null;
  const words = match[2].split('_').map((w) => (w.length <= 3 && w !== 'male' ? w.toUpperCase() : w));
  const accent = LOCALES[match[1]] ?? match[1];
  return { id, name: `${words.join(' ')}, ${accent} (Piper)`, group: 'Piper' };
}

export function describeVoice(id: string, featured: readonly { id: string; name: string }[] = []): VoiceInfo {
  const named = featured.find((f) => f.id === id);
  if (id.includes(',')) {
    const parts = id.split(',').map((p) => kokoro(p.trim())?.name.split(',')[0] ?? p.trim());
    return { id, name: named?.name ?? `Blend of ${parts.join(', ')}`, group: 'Blends' };
  }
  const piperVoice = piper(id);
  if (piperVoice) return piperVoice;
  const base = kokoro(id);
  if (base) return named ? { ...base, name: named.name } : base;
  return { id, name: named?.name ?? id, group: 'Other' };
}

const GROUP_ORDER = ['Piper', 'Blends', 'American', 'British'];

/** Every voice to offer, grouped and sorted by name, the household's own first in its group. */
export function voiceGroups(
  voices: readonly string[],
  featured: readonly { id: string; name: string }[],
): Array<{ group: string; voices: VoiceInfo[] }> {
  // Tolerate a caller that hands over a malformed answer: a spread of undefined
  // here used to throw inside render and take a whole page down.
  const ids = [
    ...new Set([
      ...(Array.isArray(featured) ? featured : []).map((f) => f?.id).filter((id): id is string => typeof id === 'string'),
      // Only strings are voices: a junk entry (or a padded-out array) is skipped.
      ...(Array.isArray(voices) ? voices.filter((id): id is string => typeof id === 'string' && id.length > 0) : []),
    ]),
  ];
  const byGroup = new Map<string, VoiceInfo[]>();
  for (const id of ids) {
    const info = describeVoice(id, featured);
    byGroup.set(info.group, [...(byGroup.get(info.group) ?? []), info]);
  }
  const rank = (g: string) => (GROUP_ORDER.includes(g) ? GROUP_ORDER.indexOf(g) : GROUP_ORDER.length);
  return [...byGroup.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([group, list]) => ({ group, voices: list.sort((x, y) => x.name.localeCompare(y.name)) }));
}
