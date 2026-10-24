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
    <section className="glass-panel rounded-2xl p-4 sm:p-5 space-y-2" data-testid="talk-request">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-white">
        <MessageCircleHeart size={16} className="text-rose-300" /> I need to talk
      </h3>
      {ask.isSuccess ? (
        <p className="text-xs text-emerald-300" data-testid="talk-request-sent">
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
                  className={`min-h-11 rounded-xl border px-3 text-xs capitalize ${on ? 'border-rose-400/40 bg-rose-400/10 text-rose-100' : 'border-white/10 bg-white/5 text-slate-400'}`}
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
            className="w-full rounded-lg border border-white/10 bg-white/5 px-2 py-1.5 text-sm text-slate-100"
          />
          <button
            type="button"
            disabled={chosen.length === 0 || ask.isPending}
            onClick={() => ask.mutate(chosen)}
            className="glass-button min-h-11 px-3 text-xs font-semibold disabled:opacity-50"
          >
            Let them know
          </button>
          {ask.isError && <p className="text-xs text-amber-300">That did not send. Try again in a moment.</p>}
        </>
      )}
    </section>
  );
}
