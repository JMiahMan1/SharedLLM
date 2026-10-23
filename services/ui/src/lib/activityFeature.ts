/* Where each route lands on the activity feed (parents-only, by design). */
const PAGE_FEATURES: Record<string, string> = {
  '/': 'dashboard',
  '/dashboard': 'dashboard',
  '/notes': 'notes',
  '/recipes': 'recipes',
  '/family': 'family',
  '/calendar': 'calendar',
  '/media': 'media',
  '/wander': 'wander',
  '/fitness': 'health',
  '/health': 'health',
  '/bible': 'bible',
  '/remote': 'remote',
  '/settings': 'settings',
};

/** The feature name a path belongs to - first segment when it is not mapped. */
export function featureForPath(pathname: string): string {
  const base = (pathname.split('?')[0].replace(/\/+$/, '') || '/').toLowerCase();
  if (PAGE_FEATURES[base]) return PAGE_FEATURES[base];
  const first = base.split('/').filter(Boolean)[0];
  return first || 'app';
}
