import type { AiCapability } from '../types/api';

export type CapabilityStatus = 'ok' | 'no' | 'unknown';

/**
 * 'unknown' is a real third state, not a fallback.
 *
 * Whether an image backend accepts a second (donor) image cannot be known
 * without attempting a real multi-image request, so the backend reports it as
 * unconfirmed. Collapsing that to yes or no is what makes a capability list
 * untrustworthy, so callers must handle it explicitly.
 */
export function statusOf(cap: AiCapability): CapabilityStatus {
  if (cap.available === true) return 'ok';
  if (cap.available === false) return 'no';
  return 'unknown';
}

export function capabilityLabel(cap: AiCapability): string {
  const s = statusOf(cap);
  if (s === 'ok') return `${cap.label} — ready`;
  if (s === 'no') return `${cap.label} — unavailable`;
  return `${cap.label} — unconfirmed`;
}

export interface CapabilitySummary {
  total: number;
  ready: number;
  unavailable: number;
  unconfirmed: number;
}

export function summarise(caps: AiCapability[]): CapabilitySummary {
  const ready = caps.filter((c) => c.available === true).length;
  const unavailable = caps.filter((c) => c.available === false).length;
  const unconfirmed = caps.filter((c) => c.available === null).length;
  return { total: caps.length, ready, unavailable, unconfirmed };
}

export function toneFor(summary: CapabilitySummary): 'ok' | 'warn' | 'neutral' {
  if (summary.unavailable > 0) return 'warn';
  if (summary.unconfirmed > 0) return 'neutral';
  return 'ok';
}
