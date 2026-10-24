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
    <section className="glass-panel rounded-2xl p-4 sm:p-5 space-y-3" data-testid="guardian-journal">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-white">
        <BookHeart size={16} className="text-violet-300" /> Talk journal
      </h2>
      <div className="space-y-2">
        <textarea
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={`What did you and ${name} talk or pray about?`}
          aria-label="New journal note"
          className="w-full rounded-lg border border-white/10 bg-white/5 px-2 py-1.5 text-sm text-slate-100"
        />
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-[11px] text-slate-400">
            <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
            Share with {name}'s other parent
          </label>
          <button
            type="button"
            disabled={!text.trim() || add.isPending}
            onClick={() => add.mutate()}
            className="glass-button ml-auto min-h-11 px-3 text-xs font-semibold disabled:opacity-50"
          >
            Save
          </button>
        </div>
      </div>
      {isLoading ? (
        <div className="h-12 skeleton rounded-xl" aria-hidden />
      ) : isError ? (
        <p className="text-xs text-amber-300">The journal could not be loaded.</p>
      ) : (data?.notes.length ?? 0) === 0 ? (
        <p className="text-xs text-slate-500">No notes yet.</p>
      ) : (
        <ul className="divide-y divide-white/5">
          {data!.notes.map((n) => (
            <li key={n.id} className="flex items-start gap-2 py-2 text-xs" data-testid={`journal-${n.id}`}>
              <span className="min-w-0 flex-1">
                <span className="block whitespace-pre-wrap text-slate-200">{n.text}</span>
                <span className="block text-[10px] text-slate-500">
                  {new Date(n.created_at).toLocaleDateString()} · {n.author === me ? 'you' : n.author}{n.shared ? ' · shared' : ''}
                </span>
              </span>
              {n.author === me && (
                <button type="button" aria-label="Delete note" onClick={() => remove.mutate(n.id)} className="min-h-11 min-w-11 flex items-center justify-center text-slate-500 hover:text-red-300">
                  <Trash2 size={13} />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
