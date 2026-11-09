import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { MessageCircleHeart } from 'lucide-react';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';

/**
 * A voice for the quiet ones (Phase 11 step 7): one tap tells the parent a
 * child picks that they would like to talk, with an optional note, so nobody
 * has to say it out loud first. Shown only to someone who has parents.
 */
export default function TalkRequestCard() {
  const isMinor = Boolean(useAuth().user?.is_minor);
  const { data } = useQuery({ queryKey: ['my-parents'], queryFn: () => api.getMyParents(), enabled: isMinor, retry: false });
  const [to, setTo] = useState<string[] | null>(null);
  const [note, setNote] = useState('');
  const ask = useMutation({ mutationFn: (who: string[]) => api.askToTalk(who, note.trim()) });

  const parents = data?.parents ?? [];
  if (!isMinor || parents.length === 0) return null;
  const chosen = to ?? parents;

  return (
    <section className="glass-panel rounded-2xl border border-white/10 p-4 sm:p-5 space-y-3" data-testid="talk-request">
      <div className="flex items-center gap-2.5 border-b border-white/5 pb-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-rose-400/20 bg-rose-400/10 text-rose-300">
          <MessageCircleHeart size={16} />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-white">I need to talk</h3>
          <p className="text-[11px] text-slate-400">Let a parent know quietly that you would like a moment together.</p>
        </div>
      </div>
      {ask.isSuccess ? (
        <p className="rounded-xl border border-emerald-400/20 bg-emerald-400/10 p-3 text-xs text-emerald-200" data-testid="talk-request-sent">
          Sent. {ask.data.told.length > 1 ? 'They' : ask.data.told[0]} will find a quiet moment with you.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Who to tell">
            {parents.map((p) => {
              const on = chosen.includes(p);
              return (
                <button
                  key={p}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setTo(on ? chosen.filter((x) => x !== p) : [...chosen, p])}
                  className={`min-h-11 rounded-xl border px-3.5 text-xs font-medium capitalize transition-colors ${
                    on ? 'border-rose-400/40 bg-rose-400/15 text-rose-100' : 'border-white/10 bg-white/5 text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {p}
                </button>
              );
            })}
          </div>
          <input
            type="text"
            value={note}
            maxLength={160}
            onChange={(e) => setNote(e.target.value)}
            placeholder="A few words if you like (optional)"
            aria-label="A short note"
            className="min-h-11 w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-rose-400/50 focus:outline-none"
          />
          <button
            type="button"
            disabled={chosen.length === 0 || ask.isPending}
            onClick={() => ask.mutate(chosen)}
            className="glass-button min-h-11 rounded-xl px-4 text-xs font-semibold disabled:opacity-50"
          >
            Let them know
          </button>
          {ask.isError && <p className="text-xs text-amber-300">That did not send. Try again in a moment.</p>}
        </>
      )}
    </section>
  );
}
