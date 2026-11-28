// How a streamed chat turn becomes the parts the transcript draws. The same
// folding rules as the gateway's TurnTranscript, so a turn looks the same while
// it streams and after it is reloaded.
import type { WorkspaceChatEvent, WorkspaceChatMessage, WorkspaceChatPart } from '../../../types/api';

export interface LiveTurn {
  parts: WorkspaceChatPart[];
  /** Text streaming for the current step; becomes a part or the answer. */
  liveText: string;
  meta: NonNullable<WorkspaceChatMessage['meta']>;
}

export const emptyTurn = (): LiveTurn => ({ parts: [], liveText: '', meta: {} });

export function foldEvent(turn: LiveTurn, event: WorkspaceChatEvent): LiveTurn {
  const parts = [...turn.parts];
  switch (event.type) {
    case 'start':
      return { ...turn, meta: { ...turn.meta, requested_mode: event.requested_mode, resolved_mode: event.resolved_mode, reason: event.reason } };
    case 'model':
      return { ...turn, meta: { ...turn.meta, model: event.model, context_chars: event.context_chars } };
    case 'step':
      // Text from a step that ended without a tool call is the answer so far;
      // a new step starts its own.
      return { ...turn, liveText: '' };
    case 'thinking': {
      const last = parts[parts.length - 1];
      if (last && last.type === 'reasoning' && last.step === event.step) {
        parts[parts.length - 1] = { ...last, text: last.text + event.text };
      } else {
        parts.push({ type: 'reasoning', text: event.text, step: event.step });
      }
      return { ...turn, parts };
    }
    case 'text':
      return { ...turn, liveText: turn.liveText + event.text };
    case 'tool_call':
      // The call replaces the raw text that announced it: keep the prose, show the call.
      if (event.preamble) parts.push({ type: 'text', text: event.preamble });
      parts.push({
        type: 'tool',
        id: event.id,
        name: event.name,
        input: event.input ?? {},
        status: 'running',
        ...(missionIdFromInput(event.input) ? { mission_id: missionIdFromInput(event.input) as number } : {}),
      });
      return { ...turn, parts, liveText: '' };
    case 'tool_result': {
      const missionId = missionIdFromOutput(event.output);
      return {
        ...turn,
        parts: parts.map((p) =>
          p.type === 'tool' && p.id === event.id
            ? {
                ...p,
                output: event.output,
                status: /^Sorry, I (couldn't|encountered)/.test(event.output) ? 'error' : 'done',
                ...(missionId ? { mission_id: missionId } : {}),
              }
            : p,
        ),
      };
    }
    case 'mission':
      parts.push({ type: 'mission', mission_id: event.mission_id, status: 'queued' });
      return { ...turn, parts, meta: { ...turn.meta, mission_id: event.mission_id } };
    default:
      return turn;
  }
}

/** A short line for a tool card: the first argument worth reading. */
export function toolSummary(input: Record<string, unknown>): string {
  for (const key of ['query', 'prompt', 'task', 'instruction', 'path', 'file_path', 'image_path', 'command', 'url', 'action', 'text', 'description']) {
    const value = input[key];
    if (typeof value === 'string' && value.trim()) return value.length > 80 ? `${value.slice(0, 80)}…` : value;
  }
  const first = Object.values(input).find((v) => typeof v === 'string' && v.trim()) as string | undefined;
  return first ? (first.length > 80 ? `${first.slice(0, 80)}…` : first) : '';
}

export type ToolKind = 'tool' | 'delegation' | 'subagent';

export interface ToolPresentation {
  kind: ToolKind;
  /** The readable name of the call. */
  label: string;
  /** The harness or system the work is handed to, when this is a handoff. */
  targetLabel?: string;
  /** Tailwind classes for the handoff header. */
  tone: string;
}

const HANDOFFS: Record<string, Omit<ToolPresentation, 'kind'> & { kind: ToolKind }> = {
  delegatesubagent: {
    kind: 'subagent',
    label: 'Explorer subagent',
    targetLabel: 'Subagent',
    tone: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-100',
  },
  delegateraven: {
    kind: 'delegation',
    label: 'Raven mission handoff',
    targetLabel: 'Raven Mission',
    tone: 'border-indigo-500/30 bg-indigo-500/10 text-indigo-100',
  },
  delegateworkspace: {
    kind: 'delegation',
    label: 'Workspace handoff',
    targetLabel: 'Workspace',
    tone: 'border-sky-500/30 bg-sky-500/10 text-sky-100',
  },
  delegatelookup: {
    kind: 'delegation',
    label: 'Lookup handoff',
    targetLabel: 'Librarian',
    tone: 'border-teal-500/30 bg-teal-500/10 text-teal-100',
  },
  delegatenotify: {
    kind: 'delegation',
    label: 'Notify handoff',
    targetLabel: 'Notifications',
    tone: 'border-amber-500/30 bg-amber-500/10 text-amber-100',
  },
};

const normalizeToolName = (name: string) => name.replace(/Request$/, '').replace(/[^a-z0-9]/gi, '').toLowerCase();

/**
 * A handoff is not an ordinary tool: say which harness is taking the work, so
 * a Raven mission or an Explorer subagent reads plainly instead of as JSON.
 */
export function toolPresentation(name: string): ToolPresentation {
  const handoff = HANDOFFS[normalizeToolName(name)];
  if (handoff) return handoff;
  return { kind: 'tool', label: toolLabel(name), tone: 'border-white/10 bg-black/30' };
}

export function isDelegationTool(name: string): boolean {
  return toolPresentation(name).kind !== 'tool';
}

/** `"WorkspaceSearchRequest"` -> `"Workspace search"`, or a handoff's own name. */
export function toolLabel(name: string): string {
  const handoff = HANDOFFS[normalizeToolName(name)];
  if (handoff) return handoff.label;
  const base = name.replace(/Request$/, '').replace(/([a-z])([A-Z])/g, '$1 $2');
  return base.charAt(0).toUpperCase() + base.slice(1).toLowerCase();
}

const MISSION_OUTPUT_PATTERNS = [
  /"mission_id"\s*:\s*"?(\d{1,12})"?/i,
  /\bmission_id\s*[=:]\s*"?(\d{1,12})"?/i,
  /\bmission\s*#\s*(\d{1,12})\b/i,
];

/**
 * The mission a tool result points at, if any. Handoffs report it as JSON, a
 * sentence, or `mission_id=7`; all three should become the same clean link.
 */
export function missionIdFromOutput(output: string | undefined): number | null {
  if (!output) return null;
  for (const pattern of MISSION_OUTPUT_PATTERNS) {
    const match = output.match(pattern);
    if (match) {
      const value = Number(match[1]);
      if (Number.isFinite(value) && value > 0) return value;
    }
  }
  return null;
}

/** A mission id passed as a tool argument, before any result arrives. */
export function missionIdFromInput(input: Record<string, unknown> | undefined): number | null {
  if (!input) return null;
  for (const key of ['mission_id', 'missionId', 'raven_mission_id', 'id']) {
    const value = input[key];
    if (typeof value === 'number' && Number.isFinite(value) && value > 0) return value;
    if (typeof value === 'string' && /^\d{1,12}$/.test(value.trim()) && key !== 'id') return Number(value);
  }
  return null;
}
