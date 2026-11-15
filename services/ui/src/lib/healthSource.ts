/**
 * Where a health reading came from, in words a person reads. The server stores
 * an id (`health_connect`, `manual`); shown as is, it read like a bug.
 */
const NAMES: Record<string, string> = {
  manual: 'hand logged',
  health_connect: 'Health Connect',
  usage_estimate: 'estimated from device use',
};

export function healthSourceName(source: string | null | undefined): string {
  if (!source) return '';
  return NAMES[source] ?? source.replace(/_/g, ' ');
}
