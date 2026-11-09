import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BookHeart, Trash2 } from 'lucide-react';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';

/**
 * A parent's talk journal about one child (Phase 11 step 7): what was talked
 * about, prayed about and agreed. Private to the writer unless shared with
 * the child's other parent; the child never sees it.
 */
export default function JournalCard({ username, name }: { username: string; name: string }) {
  const me = useAuth().user?.username;
  const queryClient = useQueryClient();
  const key = ['guardian-journal', username];
  const { data, isLoading, isError } = useQuery({ queryKey: key, queryFn: () => api.getGuardianJournal(username), retry: false });
  const [text, setText] = useState('');
  const [shared, setShared] = useState(false);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: key });
  const add = useMutation({
    mutationFn: () => api.addGuardianJournal(username, text.trim(), shared),
    onSuccess: () => {
      setText('');
      refresh();
    },
  });
  const remove = useMutation({ mutationFn: (id: number) => api.deleteGuardianJournal(username, id), onSuccess: refresh });

  return (
    <section className="glass-panel min-w-0 rounded-2xl border border-white/10 p-4 sm:p-5 space-y-3.5" data-testid="guardian-journal">
      <div className="flex items-center justify-between gap-2 border-b border-white/[0.06] pb-2.5">
        <h2 className="flex items-center gap-2.5 text-sm font-semibold tracking-tight text-white">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-violet-400/20 bg-violet-400/10">
            <BookHeart size={16} className="text-violet-300" />
          </span>
          <span>Talk journal</span>
        </h2>
        {data?.notes && data.notes.length > 0 && (
          <span className="text-[11px] font-medium text-slate-400">
            {data.notes.length} {data.notes.length === 1 ? 'note' : 'notes'}
          </span>
        )}
      </div>
      <div className="space-y-2.5 rounded-xl border border-white/10 bg-white/[0.03] p-3">
        <textarea
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={`What did you and ${name} talk or pray about?`}
          aria-label="New journal note"
          className="w-full rounded-xl border border-white/10 bg-slate-950/60 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-violet-400/50 focus:outline-none"
        />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <label className="flex min-h-11 cursor-pointer items-center gap-2.5 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={shared}
              onChange={(e) => setShared(e.target.checked)}
              className="h-4 w-4 shrink-0 rounded accent-violet-400"
            />
            <span>Share with {name}&apos;s other parent</span>
          </label>
          <button
            type="button"
            disabled={!text.trim() || add.isPending}
            onClick={() => add.mutate()}
            className="glass-button ml-auto min-h-11 px-4 text-xs font-semibold disabled:opacity-50"
          >
            Save
          </button>
        </div>
      </div>
      {isLoading ? (
        <div className="h-16 skeleton rounded-xl" aria-hidden />
      ) : isError ? (
        <p className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-3 text-xs text-amber-300">The journal could not be loaded.</p>
      ) : (data?.notes.length ?? 0) === 0 ? (
        <p className="rounded-xl border border-white/5 bg-white/[0.02] p-3 text-xs text-slate-400">No notes yet.</p>
      ) : (
        <ul className="space-y-2">
          {data!.notes.map((n) => (
            <li key={n.id} className="flex items-start gap-2.5 rounded-xl border border-white/10 bg-white/[0.03] p-3 text-xs" data-testid={`journal-${n.id}`}>
              <span className="min-w-0 flex-1 space-y-1">
                <span className="block whitespace-pre-wrap leading-relaxed text-slate-200">{n.text}</span>
                <span className="flex flex-wrap items-center gap-1.5 text-[11px] text-slate-400">
                  <span>{new Date(n.created_at).toLocaleDateString()}</span>
                  <span>·</span>
                  <span className="font-medium text-slate-300">{n.author === me ? 'you' : n.author}</span>
                  {n.shared && (
                    <span className="rounded-full border border-violet-400/30 bg-violet-400/10 px-2 py-0.5 text-[10px] font-medium text-violet-200">
                      shared
                    </span>
                  )}
                </span>
              </span>
              {n.author === me && (
                <button
                  type="button"
                  aria-label="Delete note"
                  onClick={() => remove.mutate(n.id)}
                  className="-mr-1 -mt-1 flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-xl text-slate-400 transition-colors hover:bg-white/10 hover:text-red-300"
                >
                  <Trash2 size={14} />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
