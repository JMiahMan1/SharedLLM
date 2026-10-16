/**
 * Admin > Network: what the control plane's router monitor reports
 * (services/control_plane/router_monitor.py, docs/NETWORK.md).
 */

export interface NetworkClient {
  mac: string;
  name: string;
  ip: string | null;
  signal: number | null;
  rx_mbps: number;
  tx_mbps: number;
  /** Share of the channel's time this device used since the last check. */
  airtime_pct: number | null;
  connected_s: number | null;
}

export interface NetworkRadio {
  device: string;
  ssid: string | null;
  band: string;
  channel: number | null;
  busy_pct: number | null;
  clients: NetworkClient[];
}

export interface NetworkEvent {
  time: number;
  title: string;
  body: string;
  tag: string;
}

export interface NetworkStatus {
  configured: boolean;
  connected?: boolean;
  error?: string | null;
  checked_at?: number;
  internet?: boolean;
  router?: { model?: string; hostname?: string; version?: string; uptime?: number; memory_free_pct?: number | null; load1?: number };
  wan?: { up: boolean; uptime: number };
  radios?: NetworkRadio[];
  pools?: { network: string; size: number; used: number }[];
  conntrack?: { count?: number; max?: number };
  events?: NetworkEvent[];
}

export interface RouterLogLine {
  id: number;
  time: number;
  level: string;
  msg: string;
}

export interface NetworkUsageRow {
  mac: string;
  name: string;
  ip: string | null;
  bytes: number;
}

/** "3 days", "5 hrs", "12 min" for an uptime in seconds. */
export function formatUptime(seconds: number | undefined): string {
  if (!seconds || seconds < 0) return '';
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return `${mins} min`;
  const hours = Math.floor(mins / 60);
  if (hours < 48) return `${hours} hr${hours === 1 ? '' : 's'}`;
  return `${Math.floor(hours / 24)} days`;
}

/** How a radio's channel load reads: calm, busy or congested. */
export function busyLevel(pct: number | null | undefined): 'calm' | 'busy' | 'congested' | 'unknown' {
  if (pct == null) return 'unknown';
  if (pct >= 80) return 'congested';
  if (pct >= 60) return 'busy';
  return 'calm';
}

/** A Wi-Fi signal in dBm as words people know. */
export function signalLabel(dbm: number | null | undefined): string {
  if (dbm == null) return '';
  if (dbm >= -55) return 'Excellent';
  if (dbm >= -67) return 'Good';
  if (dbm >= -75) return 'Fair';
  return 'Weak';
}
