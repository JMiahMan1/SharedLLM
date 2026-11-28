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

// ── Harness-aware event reading ─────────────────────────────────────────────
// Mission logs are a stream of action/payload/result lines written by a model:
// they are not a schema. These helpers name the SOTA harness steps when they
// can recognise them and fall back to the old behaviour when they cannot, so
// every historical log still renders exactly as it did.

export type MissionEventKind = 'tool' | 'delegation' | 'subagent' | 'parallel' | 'lint' | 'telemetry' | 'event';
export type MissionEventStatus = 'running' | 'success' | 'error' | 'warning' | 'info';

export interface MissionStep {
  /** The raw events this step draws (action, its payload, and its result). */
  events: MissionLogEvent[];
  type: string;
  kind: MissionEventKind;
  label: string;
  detail: string;
  status: MissionEventStatus;
  /** The remembered tool-call arguments, when the log carried them. */
  payload?: string;
  time: string;
}

const HANDOFFS: Record<string, { kind: 'delegation' | 'subagent'; label: string }> = {
  delegatesubagent: { kind: 'subagent', label: 'Explorer subagent' },
  delegateraven: { kind: 'delegation', label: 'Raven mission handoff' },
  delegateworkspace: { kind: 'delegation', label: 'Workspace handoff' },
  delegatelookup: { kind: 'delegation', label: 'Lookup handoff' },
  delegatenotify: { kind: 'delegation', label: 'Notify handoff' },
};

const TOOL_LABELS: Record<string, string> = {
  workspacelintrequest: 'Lint verification',
  workspacesearchrequest: 'Workspace search',
  workspacefilereadrequest: 'Workspace file read',
  workspacefilewriterequest: 'Workspace file write',
  workspacefilepatchrequest: 'Workspace file patch',
  workspacelistrequest: 'Workspace list',
};

const normalizeToolName = (name: string) => name.replace(/Request$/, '').replace(/[^a-z0-9]/gi, '').toLowerCase();

/** The tool a log line names, from either an action or its payload. */
export function actionToolName(event: MissionLogEvent): string | null {
  const type = eventType(event);
  if (type === 'action') {
    const text = logText(event.data).trim();
    const prefixed = text.match(/^(?:Executing|Running|Calling)\s+(?:tool:\s*)?([A-Za-z][\w]*Request)\b/i);
    if (prefixed) return prefixed[1];
    const bare = text.match(/\b([A-Za-z][\w]*Request)\b/);
    return bare ? bare[1] : null;
  }
  if (type === 'action_payload') {
    const parsed = parsePayload(event.data);
    if (parsed && typeof parsed === 'object') {
      const obj = parsed as Record<string, unknown>;
      for (const key of ['@type', 'type', 'tool', 'name', 'action']) {
        const value = obj[key];
        if (typeof value === 'string' && /Request$/i.test(value.trim())) return value.trim();
      }
    }
  }
  return null;
}

function parsePayload(data: unknown): unknown {
  if (typeof data !== 'string') return data;
  const text = data.trim();
  if (!text.startsWith('{') && !text.startsWith('[')) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export function missionToolLabel(name: string | null): string {
  if (!name) return 'Tool';
  const handoff = HANDOFFS[normalizeToolName(name)];
  if (handoff) return handoff.label;
  const known = TOOL_LABELS[name.toLowerCase()];
  if (known) return known;
  const base = name.replace(/Request$/, '').replace(/([a-z])([A-Z])/g, '$1 $2');
  return base.charAt(0).toUpperCase() + base.slice(1).toLowerCase();
}

/** Lint verdict from a line, or null when the line says nothing about lint. */
export function lintVerdict(event: MissionLogEvent): 'passed' | 'failed' | 'unverified' | null {
  const text = logText(event.data);
  if (/lint\s+(?:failed|failure)|lint failed/i.test(text)) return 'failed';
  if (/lint\s+unverified|not actually verified/i.test(text)) return 'unverified';
  if (/lint\s+(?:clean|passed)|post-write lint clean/i.test(text)) return 'passed';
  return null;
}

const TELEMETRY_TYPES = new Set([
  'sim',
  'sim_memory',
  'sim_lesson',
  'observation',
  'observation_mask',
  'observation_masked',
  'compaction',
  'compact',
  'fuel',
  'memory',
]);

/** SIM hints, observation masking and compaction are telemetry, not prose. */
export function isTelemetryEvent(event: MissionLogEvent): boolean {
  if (TELEMETRY_TYPES.has(eventType(event).toLowerCase())) return true;
  const text = logText(event.data);
  return /^\s*(?:sim|self-improvement memory)\b/i.test(text)
    || /\bobservation\s+mask/i.test(text)
    || /\b(?:context|conversation)\s+compact/i.test(text)
    || /\bhard[- ]compact/i.test(text)
    || /^\s*fuel\b/i.test(text);
}

export function telemetryLabel(event: MissionLogEvent): string {
  const type = eventType(event).toLowerCase();
  const text = logText(event.data);
  if (type.startsWith('sim') || /^\s*(?:sim|self-improvement memory)\b/i.test(text)) return 'SIM';
  if (type.includes('observation') || /observation\s+mask/i.test(text)) return 'Observation masked';
  if (type.includes('compact') || /compact/i.test(text)) return 'Context compacted';
  if (type === 'fuel' || /^\s*fuel\b/i.test(text)) return 'Fuel';
  return 'Memory';
}

function isParallelTool(name: string | null, text: string): boolean {
  return Boolean(name && /parallel|batch/i.test(name)) || /parallel\s+read|read-only\s+batch/i.test(text);
}

function payloadSummary(payload: string | undefined): string {
  if (!payload) return '';
  const parsed = parsePayload(payload);
  if (!parsed || typeof parsed !== 'object') return '';
  const obj = parsed as Record<string, unknown>;
  for (const key of ['task', 'prompt', 'query', 'instruction', 'path', 'command', 'message', 'reason', 'mission', 'agent']) {
    const value = obj[key];
    if (typeof value === 'string' && value.trim()) {
      const text = value.trim();
      return text.length > 120 ? `${text.slice(0, 120)}…` : text;
    }
  }
  return '';
}

function statusForResult(type: string, lint: ReturnType<typeof lintVerdict>): MissionEventStatus {
  if (lint === 'failed') return 'error';
  if (lint === 'unverified') return 'warning';
  if (type === 'result_error') return 'error';
  if (type === 'result_success') return 'success';
  return 'info';
}

function buildStep(
  events: MissionLogEvent[],
  options: { name?: string | null; payload?: string; resultText?: string; status: MissionEventStatus },
): MissionStep {
  const first = events[0];
  const type = eventType(first);
  const name = options.name ?? null;
  const rawActionText = type === 'action' ? logText(first.data).trim() : logText(first.data);
  const actionText = rawActionText.replace(/^(?:Executing|Running|Calling)\s+tool:\s*/i, '');
  const lint = events.map(lintVerdict).find((verdict) => verdict != null) ?? null;
  const handoff = name ? HANDOFFS[normalizeToolName(name)] : undefined;
  const parallel = isParallelTool(name, `${actionText} ${options.resultText ?? ''}`);

  let kind: MissionEventKind = 'tool';
  let label = missionToolLabel(name);
  if (handoff) {
    kind = handoff.kind;
    label = handoff.label;
  } else if (lint || (name && /lint/i.test(name))) {
    kind = 'lint';
    label = 'Lint verification';
  } else if (parallel) {
    kind = 'parallel';
    label = 'Parallel read batch';
  }

  let detail = options.resultText?.trim() ?? '';
  if (!detail && options.payload) detail = payloadSummary(options.payload);
  // With no recognised tool name there is no card title to carry the line, so
  // keep the event's own text rather than silently dropping an old log's action.
  if (!detail && !name) detail = rawActionText;
  if (detail.length > 240) detail = `${detail.slice(0, 240)}…`;

  return {
    events,
    type,
    kind,
    label,
    detail,
    status: lint && options.status === 'running' ? statusForResult(type, lint) : options.status,
    payload: options.payload,
    time: eventTime(first),
  };
}

/**
 * Fold the flat event log into readable harness steps. An action, the payload
 * it remembered, and the result that followed become one card; any event the
 * reader does not recognise stays a plain line, exactly as before.
 */
export function summarizeMissionEvents(events: MissionLogEvent[]): MissionStep[] {
  const steps: MissionStep[] = [];
  for (let i = 0; i < events.length; i += 1) {
    const event = events[i];
    const type = eventType(event);

    if (type === 'action') {
      const grouped = [event];
      let payload: string | undefined;
      let resultText: string | undefined;
      let status: MissionEventStatus = 'running';
      let j = i + 1;
      while (j < events.length) {
        const nextType = eventType(events[j]);
        if (nextType === 'action_payload') {
          grouped.push(events[j]);
          payload = logText(events[j].data);
          j += 1;
          continue;
        }
        if (nextType === 'result_success' || nextType === 'result_error') {
          grouped.push(events[j]);
          resultText = logText(events[j].data);
          status = statusForResult(nextType, lintVerdict(events[j]));
          j += 1;
          break;
        }
        break;
      }
      steps.push(buildStep(grouped, { name: actionToolName(event), payload, resultText, status }));
      i = j - 1;
      continue;
    }

    if (type === 'result_success' || type === 'result_error') {
      const lint = lintVerdict(event);
      if (lint) {
        steps.push(buildStep([event], { status: statusForResult(type, lint) }));
      } else {
        steps.push({
          events: [event],
          type,
          kind: 'event',
          label: '',
          detail: logText(event.data),
          status: statusForResult(type, null),
          time: eventTime(event),
        });
      }
      continue;
    }

    if (isTelemetryEvent(event)) {
      steps.push({
        events: [event],
        type,
        kind: 'telemetry',
        label: telemetryLabel(event),
        detail: logText(event.data),
        status: 'info',
        time: eventTime(event),
      });
      continue;
    }

    steps.push({
      events: [event],
      type,
      kind: 'event',
      label: '',
      detail: logText(event.data),
      status: 'info',
      time: eventTime(event),
    });
  }
  return steps;
}

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
