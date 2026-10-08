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
      parts.push({ type: 'tool', id: event.id, name: event.name, input: event.input ?? {}, status: 'running' });
      return { ...turn, parts, liveText: '' };
    case 'tool_result':
      return {
        ...turn,
        parts: parts.map((p) =>
          p.type === 'tool' && p.id === event.id
            ? { ...p, output: event.output, status: /^Sorry, I (couldn't|encountered)/.test(event.output) ? 'error' : 'done' }
            : p,
        ),
      };
    case 'mission':
      parts.push({ type: 'mission', mission_id: event.mission_id, status: 'queued' });
      return { ...turn, parts, meta: { ...turn.meta, mission_id: event.mission_id } };
    default:
      return turn;
  }
}

/** A short line for a tool card: the first argument worth reading. */
export function toolSummary(input: Record<string, unknown>): string {
  for (const key of ['query', 'path', 'file_path', 'image_path', 'command', 'url', 'action', 'text']) {
    const value = input[key];
    if (typeof value === 'string' && value.trim()) return value.length > 80 ? `${value.slice(0, 80)}…` : value;
  }
  const first = Object.values(input).find((v) => typeof v === 'string' && v.trim()) as string | undefined;
  return first ? (first.length > 80 ? `${first.slice(0, 80)}…` : first) : '';
}

/** "WorkspaceSearchRequest" -> "Workspace search". */
export function toolLabel(name: string): string {
  const base = name.replace(/Request$/, '').replace(/([a-z])([A-Z])/g, '$1 $2');
  return base.charAt(0).toUpperCase() + base.slice(1).toLowerCase();
}
