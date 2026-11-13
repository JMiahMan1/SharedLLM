import { apiClient } from '../services/api';

/**
 * Admin > Network > Sites by device: which sites each device looked up, from
 * the DNS lookups the router forwards to Jarvis (kept 30 days; admins only,
 * since it shows what each person visits).
 */

export interface SiteDevice {
  device: string;
  name: string;
  ip: string | null;
  network: string | null;
  lookups: number;
  domains: number;
  last: number;
}

export interface SiteLookup {
  device: string;
  name: string;
  ip: string | null;
  domain: string;
  lookups: number;
  last: number;
}

export interface SiteGroup {
  site: string;
  lookups: number;
  last: number;
  /** The names looked up under this site, most first. */
  domains: { domain: string; lookups: number }[];
}

export async function getSiteDevices(hours: number): Promise<{ hours: number; devices: SiteDevice[]; receiving: boolean }> {
  return (await apiClient.get('/api/network/sites/devices', { params: { hours } })).data;
}

export async function getDeviceSites(hours: number, device: string): Promise<{ sites: SiteLookup[] }> {
  return (await apiClient.get('/api/network/sites', { params: { hours, device, limit: 2000 } })).data;
}

/** Suffixes that take two labels, so "bbc.co.uk" is one site and not "co.uk". */
const TWO_LABEL_SUFFIXES = new Set([
  'co.uk', 'org.uk', 'ac.uk', 'gov.uk', 'me.uk', 'com.au', 'net.au', 'org.au', 'co.nz', 'co.jp', 'com.br',
  'com.mx', 'co.in', 'co.za', 'com.cn', 'com.tw', 'co.kr', 'com.sg', 'com.tr', 'com.ar',
]);

/** The site a name belongs to: "i.scdn.co" is scdn.co, "www.bbc.co.uk" is bbc.co.uk; an address is itself. */
export function siteOf(domain: string): string {
  const name = domain.toLowerCase().replace(/\.$/, '');
  if (/^[\d.]+$/.test(name) || name.includes(':')) return name;
  const labels = name.split('.').filter(Boolean);
  if (labels.length <= 2) return labels.join('.');
  const lastTwo = labels.slice(-2).join('.');
  return TWO_LABEL_SUFFIXES.has(lastTwo) ? labels.slice(-3).join('.') : lastTwo;
}

/** A device's lookups grouped by site, most looked up first. */
export function groupBySite(rows: Pick<SiteLookup, 'domain' | 'lookups' | 'last'>[]): SiteGroup[] {
  const groups = new Map<string, SiteGroup>();
  for (const row of rows) {
    const site = siteOf(row.domain);
    const g = groups.get(site) ?? { site, lookups: 0, last: 0, domains: [] };
    g.lookups += row.lookups;
    g.last = Math.max(g.last, row.last);
    g.domains.push({ domain: row.domain, lookups: row.lookups });
    groups.set(site, g);
  }
  const out = [...groups.values()];
  for (const g of out) g.domains.sort((a, b) => b.lookups - a.lookups);
  return out.sort((a, b) => b.lookups - a.lookups || a.site.localeCompare(b.site));
}

/** "just now", "12 min ago", "3 h ago", "2 days ago" from a Unix time. */
export function lastSeen(unixSeconds: number, now: number = Date.now()): string {
  const s = Math.max(0, Math.round(now / 1000 - unixSeconds));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86_400) return `${Math.round(s / 3600)} h ago`;
  const d = Math.round(s / 86_400);
  return `${d} day${d === 1 ? '' : 's'} ago`;
}
