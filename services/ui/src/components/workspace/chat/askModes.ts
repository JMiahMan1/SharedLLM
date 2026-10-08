import type { ResolvedWorkspaceAskMode, WorkspaceAskMode } from '../../../types/api';

/**
 * The workspace-chat jobs, in the order a person reads them.
 *
 * `auto` is first on purpose: it is the default, and it resolves server-side
 * from the shape of the message. The other three are the escape hatch, for when
 * the guess is wrong -- which it will be, because a keyword classifier cannot
 * tell "read the logs" (a systems task) from "what did Macduff say about
 * poverty" (a library question).
 */
export const ASK_MODES: { id: WorkspaceAskMode; label: string; blurb: string; placeholder: string }[] = [
  { id: 'auto', label: 'Auto', blurb: 'Pick from the message', placeholder: 'Ask something, or describe a task to run in this workspace…' },
  { id: 'librarian', label: 'Ask', blurb: 'Answer from your books, files and notes', placeholder: 'Ask a question. Jarvis searches your library, Nextcloud and lessons first…' },
  { id: 'single_task', label: 'Task', blurb: 'One job, a few tool steps, no retrieval', placeholder: 'Describe one job for the assistant…' },
  { id: 'raven', label: 'Raven', blurb: 'Autonomous mission, runs in the background', placeholder: 'Describe a task for Raven to run in this workspace…' },
];

export const ASK_MODE_CHIPS: Record<ResolvedWorkspaceAskMode, string> = {
  librarian: 'bg-teal-500/15 text-teal-300 border-teal-500/40',
  single_task: 'bg-sky-500/15 text-sky-300 border-sky-500/40',
  raven: 'bg-indigo-500/15 text-indigo-300 border-indigo-500/40',
};

export const RESOLVED_MODE_LABEL: Record<ResolvedWorkspaceAskMode, string> = {
  librarian: 'Ask',
  single_task: 'Task',
  raven: 'Raven',
};

export function askModeMeta(mode: WorkspaceAskMode) {
  return ASK_MODES.find((m) => m.id === mode) ?? ASK_MODES[0];
}
