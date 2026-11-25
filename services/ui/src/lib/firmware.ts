/**
 * The Firmware page's rows: one per updateable thing, as HomeHub reports them.
 *
 * Two kinds share a row shape: an `update` entity (versions, an install the
 * integration can run) and a device that only knows its own version and can
 * fetch an image from its own update address (Tasmota's `Upgrade 1`).
 */

export type FirmwareAction = 'install' | 'upgrade';

export interface FirmwareRow {
  id: string;
  entity_id: string | null;
  device_id: string | null;
  device: string | null;
  name: string;
  integration: string | null;
  model: string | null;
  area_id?: string | null;
  installed_version: string | null;
  latest_version: string | null;
  available: boolean;
  upgradable?: boolean;
  installable: boolean;
  in_progress: boolean;
  progress?: number | null;
  skipped_version?: string | null;
  release_url: string | null;
  release_summary?: string | null;
  source: 'entity' | 'device';
  action: FirmwareAction;
}

export interface FirmwareReport {
  devices: FirmwareRow[];
  installing: number;
}

/** "2026.10.17" or "not reported" for a missing version. */
export function versionText(version: string | null | undefined): string {
  const v = (version ?? '').trim();
  return v || 'not reported';
}

/** The one line under a device's name: what it runs and, when offered, what it would move to. */
export function firmwareLine(row: FirmwareRow): string {
  const installed = versionText(row.installed_version);
  if (row.available && row.latest_version) return `${installed} to ${row.latest_version}`;
  if (row.action === 'upgrade') return `${installed}, updated from the device's own update address`;
  return installed;
}

/** Which badge a row shows: an offer, a finished one, a running install, or a device that cannot be told. */
export function firmwareStatus(row: FirmwareRow): 'busy' | 'available' | 'current' | 'manual' | 'unknown' {
  if (row.in_progress) return 'busy';
  if (row.available) return 'available';
  if (row.action === 'upgrade') return row.upgradable ? 'manual' : 'unknown';
  return 'current';
}
