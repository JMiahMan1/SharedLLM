import type { ResolvedWorkspaceAskMode, WorkspaceAskMode } from '../../../types/api';

export interface AskModeMeta {
  id: WorkspaceAskMode;
  /** The full, readable harness name. */
  label: string;
  /** A short name for narrow screens where the full label would wrap. */
  shortLabel: string;
  /** One sentence: what this harness is for. */
  blurb: string;
  /** The capability line shown under the mode picker. */
  hint: string;
  /** Compact capability badges, shown only where there is room. */
  capabilities: string[];
  placeholder: string;
}

/**
 * The workspace-chat harnesses, in the order a person reads them.
 *
 * `auto` is first on purpose: it is the default, and it resolves server-side
 * from the shape of the message. The other three are the escape hatch, for when
 * the guess is wrong -- which it will be, because a keyword classifier cannot
 * tell "read the logs" (a systems task) from "what did Macduff say about
 * poverty" (a library question).
 */
export const ASK_MODES: AskModeMeta[] = [
  {
    id: 'auto',
    label: 'Auto',
    shortLabel: 'Auto',
    blurb: 'Pick the best harness from the message',
    hint: 'Auto picks Librarian, Single Task, or Raven Mission from what you asked.',
    capabilities: ['Picks a harness', 'Shows its choice'],
    placeholder: 'Ask something, or describe a task to run in this workspace…',
  },
  {
    id: 'librarian',
    label: 'Librarian',
    shortLabel: 'Library',
    blurb: 'Answer from your books, files and notes',
    hint: 'Library, retrieval and workspace tools, with visible thinking.',
    capabilities: ['Library', 'RAG', 'Workspace tools'],
    placeholder: 'Ask a question. Jarvis searches your library, Nextcloud and lessons first…',
  },
  {
    id: 'single_task',
    label: 'Single Task',
    shortLabel: 'Task',
    blurb: 'One job, a few tool steps, no retrieval',
    hint: 'Fast workspace tools with visible thinking; no library retrieval.',
    capabilities: ['Fast', 'Workspace tools', 'Thinking'],
    placeholder: 'Describe one job for the assistant…',
  },
  {
    id: 'raven',
    label: 'Raven Mission',
    shortLabel: 'Raven',
    blurb: 'Autonomous multi-step mission with lint/test verification',
    hint: 'Autonomous multi-step agent that verifies writes with lint and tests.',
    capabilities: ['Autonomous', 'Lint & tests', 'Background mission'],
    placeholder: 'Describe a task for Raven to run in this workspace…',
  },
];

export const ASK_MODE_CHIPS: Record<ResolvedWorkspaceAskMode, string> = {
  librarian: 'bg-teal-500/15 text-teal-300 border-teal-500/40',
  single_task: 'bg-sky-500/15 text-sky-300 border-sky-500/40',
  raven: 'bg-indigo-500/15 text-indigo-300 border-indigo-500/40',
};

export const RESOLVED_MODE_LABEL: Record<ResolvedWorkspaceAskMode, string> = {
  librarian: 'Librarian',
  single_task: 'Single Task',
  raven: 'Raven Mission',
};

export function askModeMeta(mode: WorkspaceAskMode): AskModeMeta {
  return ASK_MODES.find((m) => m.id === mode) ?? ASK_MODES[0];
}
