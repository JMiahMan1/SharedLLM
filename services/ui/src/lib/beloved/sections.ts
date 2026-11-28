// Which section of Beloved an address or a notice asks for, kept free of React so
// the mapping is testable on its own. The server builds `?beloved=<section>`
// (services/common/notice_links.py BELOVED_SECTIONS); "1" is what every notice
// before sections existed carried, and still means "open Beloved".
export type BelovedTab = 'home' | 'days' | 'today' | 'notes' | 'dates' | 'justus';

const BY_NAME: Record<string, BelovedTab> = {
  '1': 'home',
  home: 'home',
  us: 'home',
  day: 'days',
  days: 'days',
  today: 'today',
  note: 'notes',
  notes: 'notes',
  prayer: 'notes',
  prayers: 'notes',
  date: 'dates',
  dates: 'dates',
  idea: 'dates',
  ideas: 'dates',
  plan: 'justus',
  plans: 'justus',
  justus: 'justus',
  'just-us': 'justus',
};

/**
 * The section an address asks for, or null when it asks for none (or for
 * something this build does not have - a newer server's section opens Beloved
 * at its home rather than nowhere).
 */
export function belovedTab(value: string | null | undefined): BelovedTab | null {
  if (typeof value !== 'string') return null;
  const key = value.trim().toLowerCase();
  if (!key) return null;
  return BY_NAME[key] ?? null;
}

/** Where the section sits in the bar, so a test can check the order. */
export const BELOVED_TABS: ReadonlyArray<{ id: BelovedTab; label: string }> = [
  { id: 'home', label: 'Us' },
  { id: 'days', label: 'Days' },
  { id: 'today', label: 'Today' },
  { id: 'notes', label: 'Notes' },
  { id: 'dates', label: 'Dates' },
  { id: 'justus', label: 'Just us' },
];
