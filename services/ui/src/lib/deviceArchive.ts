import { apiClient } from '../services/api';

/**
 * Devices > Setup > Archived devices (admins). Devices that went without contact for a long time are
 * archived: never set up or retried again, out of voice and every device list, but kept as they were. They
 * come back by themselves when they show up again, or with Restore; a year later they are deleted.
 *
 * Two sources, one list: the hub's devices and integrations (services/homehub/device_archive.py, through
 * `/api/homehub/archive`) and the phones, PCs and satellites registered with Jarvis
 * (services/identity/device_archive.py, through `/api/user-panel/devices/archive`).
 */

export interface ArchiveSettings {
  enabled: boolean;
  archive_after_days: number;
  delete_after_days: number;
}

/** One archived item. `kind` says where it lives: a hub entry or device, or a device registered with Jarvis. */
export interface ArchivedItem {
  kind: 'entry' | 'device' | 'identity';
  id: string;
  name: string;
  label?: string | null;
  domain?: string | null;
  area?: string | null;
  /** A registered device's kind (phone, tablet, assistant...) and owner. */
  device_kind?: string | null;
  owner?: string | null;
  last_seen: string | null;
  archived_at: string | null;
  delete_on: string | null;
  entities?: number;
}

export interface ArchiveAudit {
  at: string;
  action: string;
  kind?: string;
  id?: string;
  name?: string;
  why?: string;
}

export interface HubArchive {
  settings: ArchiveSettings;
  items: ArchivedItem[];
  audit: ArchiveAudit[];
  last_delete_run?: string | null;
}

export interface IdentityArchive {
  settings: ArchiveSettings;
  items: ArchivedItem[];
}

export async function getHubArchive(): Promise<HubArchive> {
  const resp = await apiClient.get('/api/homehub/archive');
  return resp.data;
}

export async function getIdentityArchive(): Promise<IdentityArchive> {
  const resp = await apiClient.get('/api/user-panel/devices/archive');
  return resp.data;
}

export async function saveArchiveSettings(settings: Partial<ArchiveSettings>): Promise<void> {
  // One rule for both: the hub's devices and the devices registered with Jarvis.
  await Promise.all([
    apiClient.put('/api/homehub/archive/settings', settings),
    apiClient.put('/api/user-panel/devices/archive/settings', settings),
  ]);
}

export async function restoreArchived(item: ArchivedItem): Promise<void> {
  if (item.kind === 'identity') {
    await apiClient.post(`/api/user-panel/devices/archive/${encodeURIComponent(item.id)}/restore`);
  } else {
    await apiClient.post('/api/homehub/archive/restore', { kind: item.kind, id: item.id });
  }
}

export async function deleteArchived(item: ArchivedItem): Promise<void> {
  if (item.kind === 'identity') {
    await apiClient.delete(`/api/user-panel/devices/archive/${encodeURIComponent(item.id)}`);
  } else {
    await apiClient.post('/api/homehub/archive/delete', { kind: item.kind, id: item.id }, { timeout: 60_000 });
  }
}

/** Both lists as one, newest archive first. */
export function mergeArchived(hub: ArchivedItem[] | undefined, identity: ArchivedItem[] | undefined): ArchivedItem[] {
  return [...(hub ?? []), ...(identity ?? [])].sort((a, b) => (b.archived_at ?? '').localeCompare(a.archived_at ?? '')
    || a.name.localeCompare(b.name));
}

/** "2026-03-04T..." -> "4 Mar 2026"; empty for none. */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

/** Whole days from now until `iso` (0 when it is today or past). */
export function daysUntil(iso: string | null | undefined, now = Date.now()): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.ceil((t - now) / 86_400_000));
}

/** What the row says about a device's deletion. */
export function deletionText(item: ArchivedItem, now = Date.now()): string {
  const days = daysUntil(item.delete_on, now);
  if (days === null) return 'Not scheduled for deletion';
  if (days === 0) return 'Deleted at the next daily check';
  return `Deleted on ${shortDate(item.delete_on)} (in ${days} day${days === 1 ? '' : 's'}) unless it comes back`;
}

/** Where it comes from: the integration for a hub device, the kind and owner for a registered one. */
export function sourceText(item: ArchivedItem): string {
  if (item.kind === 'identity') {
    const kind = item.device_kind ? item.device_kind.charAt(0).toUpperCase() + item.device_kind.slice(1) : 'Device';
    return item.owner ? `${kind}, ${item.owner}'s` : kind;
  }
  return [item.label || item.domain, item.area].filter(Boolean).join(' · ');
}

/** The settings form's check: whole days in range. Answers a message, or null when they are fine. */
export function settingsProblem(s: { archive_after_days: number; delete_after_days: number }): string | null {
  const whole = (n: number) => Number.isInteger(n);
  if (!whole(s.archive_after_days) || s.archive_after_days < 7 || s.archive_after_days > 3650) {
    return 'Archive after must be from 7 to 3650 days.';
  }
  if (!whole(s.delete_after_days) || s.delete_after_days < 30 || s.delete_after_days > 3650) {
    return 'Delete after must be from 30 to 3650 days.';
  }
  return null;
}
