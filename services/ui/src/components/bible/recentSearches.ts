/**
 * The last few Bible searches on this device, newest first. A convenience
 * only: storage can be missing (a private window) and then nothing is kept.
 */
const KEY = 'jarvis_bible_recent_searches';
export const RECENT_MAX = 6;

export function recentSearches(): string[] {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? '[]');
    return Array.isArray(saved) ? saved.filter((s): s is string => typeof s === 'string').slice(0, RECENT_MAX) : [];
  } catch {
    return [];
  }
}

export function rememberSearch(term: string): string[] {
  const value = term.trim();
  if (value.length < 2) return recentSearches();
  const next = [value, ...recentSearches().filter((s) => s.toLowerCase() !== value.toLowerCase())].slice(0, RECENT_MAX);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* nothing kept; the search itself still runs */
  }
  return next;
}

export function forgetSearches(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nothing to clear */
  }
}
