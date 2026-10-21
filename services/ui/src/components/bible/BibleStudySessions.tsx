import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpenCheck, Loader2, Plus, Trash2 } from 'lucide-react';

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
  const [error, setError] = useState('');

  const sessions = useQuery({
    queryKey: ['bible-study-sessions'],
    queryFn: () => api.listBibleStudySessions(),
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
