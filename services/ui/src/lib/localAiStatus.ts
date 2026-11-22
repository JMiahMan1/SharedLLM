import { apiClient } from '../services/api';

/**
 * Whether Jarvis's local AI (the "thinking brain" on the GPU computer) can be
 * reached, from the gateway's `GET /api/status/local-ai`. The gateway learns it
 * from its own calls and a background probe, so this is a cheap read; it never
 * names a host or an error, only what is offline and what still works.
 */
export interface LocalAiStatus {
  offline: boolean;
  /** The gateway's own sentence for chat while it is offline. */
  message: string | null;
  stillWorking: string[];
  /** Other services on the same computer that are offline (arcade, audio). */
  otherOffline: string[];
}

/** Polled slowly while all is well, faster while it is offline so the banner clears soon after. */
export const POLL_UP_MS = 60_000;
export const POLL_DOWN_MS = 15_000;

const OTHER_LABELS: Record<string, string> = { arcade: 'games', audio: 'music making' };

/** The status from a raw answer, or null when the answer is not one. */
export function parseLocalAiStatus(data: unknown): LocalAiStatus | null {
  const body = data as {
    local_ai?: { offline?: unknown; message?: unknown; still_working?: unknown };
    backends?: Record<string, { state?: unknown }>;
  } | null;
  const llm = body?.local_ai;
  if (!llm || typeof llm.offline !== 'boolean') return null;
  const otherOffline = Object.entries(body?.backends ?? {})
    .filter(([name, b]) => name in OTHER_LABELS && b?.state === 'down')
    .map(([name]) => OTHER_LABELS[name]);
  return {
    offline: llm.offline,
    message: typeof llm.message === 'string' ? llm.message : null,
    stillWorking: Array.isArray(llm.still_working) ? llm.still_working.filter((s): s is string => typeof s === 'string') : [],
    otherOffline,
  };
}

/** The status, or null when it cannot be read (never an error to show: the banner just stays away). */
export async function getLocalAiStatus(): Promise<LocalAiStatus | null> {
  try {
    const resp = await apiClient.get('/api/status/local-ai', { silent: true, timeout: 5_000 });
    return parseLocalAiStatus(resp.data);
  } catch {
    return null;
  }
}

/** The line the banner shows, or null for no banner. Plain words, no host names. */
export function bannerText(status: LocalAiStatus | null): string | null {
  if (!status) return null;
  if (status.offline) {
    const others = status.otherOffline.length ? ` ${capitalise(joinList(status.otherOffline))} ${status.otherOffline.length > 1 ? 'are' : 'is'} offline too.` : '';
    return (
      "Jarvis's thinking brain is offline right now. Home control, the time and the weather still work; " +
      "questions need it and will work again when it's back." + others
    );
  }
  if (status.otherOffline.length) {
    return `${capitalise(joinList(status.otherOffline))} ${status.otherOffline.length > 1 ? 'are' : 'is'} offline right now.`;
  }
  return null;
}

function joinList(items: string[]): string {
  return items.length <= 1 ? (items[0] ?? '') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

function capitalise(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}
