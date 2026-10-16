/**
 * One way to read a Raven mission's event log, shared by the live trace, the
 * audit log and the mission detail dialog (components/settings/MissionTimeline).
 */
import { downloadBlobUrl } from './artifactKinds';

export interface MissionLogEvent {
  type?: string;
  raw_type?: string;
  data?: unknown;
  /** Seconds since the epoch, when the server recorded one. */
  timestamp?: number;
}

/**
 * The text of one event, whatever arrived.
 *
 * The server flattens streamed content parts before it stores them, so this is
 * normally already a string. It is still rendered defensively: a shape the
 * viewer did not expect used to take the whole panel down with React error #31
 * ("objects are not valid as a React child") instead of showing one odd line.
 */
export function logText(data: unknown): string {
  if (typeof data === 'string') return data;
  if (data == null) return '';
  if (typeof data === 'object' && 'text' in data) {
    const text = (data as { text?: unknown }).text;
    if (typeof text === 'string') return text;
  }
  return typeof data === 'object' ? JSON.stringify(data) : String(data);
}

export const eventType = (e: MissionLogEvent) => e.raw_type || e.type || 'system';

/** A stored `output_log` (a JSON array of events) as events; anything else as one line. */
export function parseMissionLog(outputLog: string | null | undefined): MissionLogEvent[] {
  if (!outputLog) return [];
  try {
    const parsed: unknown = JSON.parse(outputLog);
    if (Array.isArray(parsed)) return parsed as MissionLogEvent[];
  } catch {
    // Not JSON: older missions stored plain text.
  }
  return [{ type: 'system', data: outputLog }];
}

/** HH:MM:SS of when the event happened, or '' when the server did not say. */
export function eventTime(e: MissionLogEvent): string {
  return typeof e.timestamp === 'number' ? new Date(e.timestamp * 1000).toLocaleTimeString() : '';
}

const COLORS: Record<string, string> = {
  reasoning: 'text-blue-400',
  action: 'text-yellow-400 font-bold',
  action_payload: 'text-yellow-300/80',
  result_success: 'text-emerald-400 font-bold',
  result_error: 'text-red-400 font-bold',
};

export const eventColor = (type: string) => COLORS[type] ?? 'text-slate-400';

/** The events as plain text, for a download. */
export function missionLogText(events: MissionLogEvent[], result?: string | null): string {
  const lines = events.map((e) => {
    const t = eventTime(e);
    return `${t ? `[${t}] ` : ''}[${eventType(e)}] ${logText(e.data)}`;
  });
  if (result) lines.push('', '--- MISSION RESULT ---', result);
  return lines.join('\n') || 'No logs available.';
}

export function downloadMissionLog(missionId: number | string, events: MissionLogEvent[], result?: string | null) {
  const url = URL.createObjectURL(new Blob([missionLogText(events, result)], { type: 'text/plain' }));
  downloadBlobUrl(url, `mission-${missionId}-logs.txt`);
  // The click has been dispatched; release the blob once the browser has it.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
