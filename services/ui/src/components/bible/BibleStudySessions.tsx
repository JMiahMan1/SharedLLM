import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpenCheck, Download, Loader2, Plus, Search, Trash2 } from 'lucide-react';

import { api } from '../../services/api';
import type { BibleStudySession } from '../../types/api';
import { errorText } from '../../lib/errorText';
import JarvisStudyThread from './JarvisStudyThread';

interface Props {
  reference: string;
  version: string;
  edition?: string;
  onOpenRef?: (ref: string) => void;
}

function turnFromSession(turn: NonNullable<BibleStudySession['turns']>[number]) {
  let references: string[] = [];
  try {
    const parsed = JSON.parse(turn.references || '[]');
    if (Array.isArray(parsed)) references = parsed.map(String);
  } catch {
    // A hand-edited row should not break the whole thread.
  }
  return {
    question: turn.question,
    text: turn.answer,
    failed: false,
    verses: 0,
    notes: 0,
    references,
    streaming: false,
  };
}

function studyMarkdown(session: BibleStudySession): string {
  const lines = [
    `# ${session.title || session.reference}`,
    '',
    `- Reference: ${session.reference}`,
    `- Translation: ${session.version || 'default'}`,
    ...(session.edition ? [`- Study edition: ${session.edition}`] : []),
    '',
  ];
  for (const turn of session.turns ?? []) {
    let references: string[] = [];
    try {
      const parsed = JSON.parse(turn.references || '[]');
      if (Array.isArray(parsed)) references = parsed.map(String);
    } catch {
      // Keep the exported study readable even if a row was hand-edited.
    }
    lines.push(`## ${turn.question}`, '', turn.answer, '');
    if (references.length) lines.push(`References: ${references.join(', ')}`, '');
  }
  return lines.join('\n');
}

/**
 * Several Bible studies at once.
 *
 * Each session is its own reference and thread, stored in the bible service.
 * Opening a second passage no longer replaces the first conversation, and the
 * same grounded ask path still decides every answer.
 */
export default function BibleStudySessions({ reference, version, edition, onOpenRef }: Props) {
  const client = useQueryClient();
  const [openId, setOpenId] = useState<number | null>(null);
  const [memoryQuery, setMemoryQuery] = useState('');
  const [exportWorkspace, setExportWorkspace] = useState('');
  const [exportMessage, setExportMessage] = useState('');
  const [error, setError] = useState('');

  const sessions = useQuery({
    queryKey: ['bible-study-sessions'],
    queryFn: () => api.listBibleStudySessions(),
    retry: false,
  });

  const memory = useQuery({
    queryKey: ['bible-study-memory', memoryQuery.trim()],
    queryFn: () => api.searchBibleStudyMemory(memoryQuery.trim()),
    enabled: memoryQuery.trim().length >= 2,
    retry: false,
  });

  const workspaces = useQuery({
    queryKey: ['workspaces'],
    queryFn: () => api.getWorkspaces(),
    enabled: openId !== null,
    retry: false,
  });

  const detail = useQuery({
    queryKey: ['bible-study-session', openId],
    queryFn: () => api.getBibleStudySession(openId as number),
    enabled: openId !== null,
    retry: false,
  });

  const create = useMutation({
    mutationFn: () =>
      api.createBibleStudySession({
        reference,
        version,
        edition,
        title: reference,
      }),
    onSuccess: (result) => {
      setError('');
      void client.invalidateQueries({ queryKey: ['bible-study-sessions'] });
      setOpenId(result.session.id);
    },
    onError: (err) => setError(errorText(err, 'Could not start that study.')),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteBibleStudySession(id),
    onSuccess: (_result, id) => {
      if (openId === id) setOpenId(null);
      void client.invalidateQueries({ queryKey: ['bible-study-sessions'] });
    },
    onError: (err) => setError(errorText(err, 'Could not close that study.')),
  });

  const saveTurn = useMutation({
    mutationFn: ({ id, question, answer, references }: { id: number; question: string; answer: string; references: string[] }) =>
      api.addBibleStudyTurn(id, { question, answer, references }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['bible-study-sessions'] });
      void client.invalidateQueries({ queryKey: ['bible-study-session', openId] });
    },
  });

  const open = detail.data?.session;

  async function exportStudy() {
    if (!open) return;
    const workspaceId = exportWorkspace || workspaces.data?.[0]?.id || '';
    if (!workspaceId) {
      setExportMessage('No workspace is available to export into.');
      return;
    }
    setExportMessage('Exporting…');
    const safeRef = (open.reference || `study-${open.id}`)
      .replace(/[^a-z0-9._-]+/gi, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 60);
    try {
      await api.writeWorkspaceFile(
        workspaceId,
        `bible-studies/${safeRef || 'study'}-${open.id}.md`,
        studyMarkdown(open),
      );
      setExportMessage('Study exported to the workspace.');
    } catch (err) {
      setExportMessage(errorText(err, 'Could not export that study.'));
    }
  }

  return (
    <section className="space-y-3" data-testid="bible-study-sessions">
      <header className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-bold text-white flex items-center gap-2">
            <BookOpenCheck size={16} className="text-sky-300" />
            My Bible studies
          </h2>
          <p className="mt-1 text-xs text-slate-400">
            Keep several passages open at once. Each study keeps its own questions and grounded answers.
          </p>
        </div>
        <button
          type="button"
          onClick={() => create.mutate()}
          disabled={create.isPending || !reference}
          className="min-h-10 rounded-xl bg-sky-500/20 px-3 text-xs font-medium text-sky-200 disabled:opacity-50 inline-flex items-center gap-1.5"
        >
          {create.isPending ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
          Study {reference || 'this passage'}
        </button>
      </header>

      {error ? <p className="text-xs text-rose-300">{error}</p> : null}

      <div className="rounded-xl border border-white/5 bg-white/[0.02] p-3" data-testid="bible-study-memory">
        <label htmlFor="bible-study-memory-search" className="sr-only">
          Search your study memory
        </label>
        <div className="flex items-center gap-2">
          <Search size={14} className="text-slate-500 shrink-0" />
          <input
            id="bible-study-memory-search"
            type="search"
            value={memoryQuery}
            onChange={(event) => setMemoryQuery(event.target.value)}
            placeholder="Search past studies and answers…"
            className="min-h-10 min-w-0 flex-1 bg-transparent text-xs text-slate-200 outline-none placeholder:text-slate-500"
          />
        </div>
        {memoryQuery.trim().length >= 2 ? (
          memory.isLoading ? (
            <p className="mt-2 text-[11px] text-slate-500">Searching…</p>
          ) : (memory.data?.results ?? []).length === 0 ? (
            <p className="mt-2 text-[11px] text-slate-500">No saved study matches that yet.</p>
          ) : (
            <ul className="mt-2 space-y-1.5">
              {(memory.data?.results ?? []).map((result) => (
                <li key={`${result.session_id}-${result.created_at}-${result.question.slice(0, 24)}`}>
                  <button
                    type="button"
                    onClick={() => setOpenId(result.session_id)}
                    className="w-full rounded-lg border border-white/5 bg-black/20 p-2 text-left hover:border-sky-400/30"
                  >
                    <span className="block text-[10px] uppercase tracking-wider text-sky-300/80">
                      {result.title || result.reference}
                    </span>
                    <span className="mt-0.5 block text-xs text-slate-200 line-clamp-2">{result.question}</span>
                    <span className="mt-0.5 block text-[11px] text-slate-500 line-clamp-2">{result.answer}</span>
                  </button>
                </li>
              ))}
            </ul>
          )
        ) : null}
      </div>

      {sessions.isLoading ? (
        <p className="text-xs text-slate-500">Loading studies…</p>
      ) : (sessions.data?.sessions ?? []).length === 0 ? (
        <p className="text-xs text-slate-500">No studies yet. Start one from the passage you are reading.</p>
      ) : (
        <ul className="grid gap-2 sm:grid-cols-2">
          {(sessions.data?.sessions ?? []).map((session) => (
            <li
              key={session.id}
              className="rounded-xl border border-white/5 bg-white/[0.02] p-3 flex items-start gap-2"
              data-testid={`bible-study-session-${session.id}`}
            >
              <button type="button" onClick={() => setOpenId(session.id)} className="min-w-0 flex-1 text-left">
                <span className="block truncate text-xs font-medium text-slate-200">{session.title || session.reference}</span>
                <span className="block truncate text-[10px] text-slate-500">
                  {session.reference} · {session.version || 'default translation'}
                </span>
              </button>
              <button
                type="button"
                aria-label={`Close ${session.title || session.reference}`}
                onClick={() => remove.mutate(session.id)}
                className="min-h-9 min-w-9 rounded-lg text-rose-300/80 hover:bg-rose-500/10"
              >
                <Trash2 size={13} className="mx-auto" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {openId !== null && detail.isLoading ? (
        <p className="text-xs text-slate-500 flex items-center gap-2">
          <Loader2 size={13} className="animate-spin" /> Opening study…
        </p>
      ) : null}

      {openId !== null && open ? (
        <>
          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-white/5 bg-white/[0.02] p-3">
            <label htmlFor="bible-study-export-workspace" className="sr-only">
              Export study to workspace
            </label>
            <select
              id="bible-study-export-workspace"
              value={exportWorkspace}
              onChange={(event) => setExportWorkspace(event.target.value)}
              className="min-h-10 min-w-0 flex-1 rounded-lg bg-slate-800/80 border border-white/10 px-3 text-xs text-slate-200"
            >
              <option value="">Choose a workspace…</option>
              {(workspaces.data ?? []).map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.display_name}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => void exportStudy()}
              disabled={!(exportWorkspace || workspaces.data?.length)}
              className="min-h-10 rounded-lg bg-sky-500/20 px-3 text-xs font-medium text-sky-200 disabled:opacity-50 inline-flex items-center gap-1.5"
            >
              <Download size={13} />
              Export to workspace
            </button>
          </div>
          {exportMessage ? (
            <p className="text-xs text-slate-400" data-testid="bible-study-export-message">
              {exportMessage}
            </p>
          ) : null}
        </>
      ) : null}

      {openId !== null && open ? (
        <JarvisStudyThread
          passage={open.reference}
          version={open.version || version}
          edition={open.edition || edition}
          onClose={() => setOpenId(null)}
          onOpenRef={onOpenRef}
          initialTurns={(open.turns ?? []).map(turnFromSession)}
          onTurnSaved={(turn) =>
            saveTurn.mutate({
              id: open.id,
              question: turn.question,
              answer: turn.text,
              references: turn.references,
            })
          }
        />
      ) : null}
    </section>
  );
}
