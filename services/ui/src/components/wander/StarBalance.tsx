import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Sparkles, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { AdminStarGrantResponse, StarGrant, StarMirrorOutcome } from '../../types/api';

const REASONS = [
  { id: 'bonus', label: 'Bonus' },
  { id: 'achievement', label: 'Achievement' },
  { id: 'game', label: 'Game' },
  { id: 'chore', label: 'Chore' },
  { id: 'manual', label: 'Just because' },
];

/**
 * Bonus stars: balance plus the ledger of who granted what.
 *
 * Grants land in Jarvis immediately; the Skylight write-through is reported
 * by the backend rather than assumed, so a grant is never lost to a Skylight
 * outage and never claimed as delivered when it was not.
 *
 * On an admin surface (`admin`) the grant goes through the route that mirrors
 * to the target's Skylight account and reports both halves separately. Off that
 * surface it keeps the plain ledger route, so nothing here assumes the caller
 * is an admin or that a mirror is configured.
 */
export default function StarBalance({ userId, admin = false }: { userId?: string; admin?: boolean }) {
  const queryClient = useQueryClient();
  const [amount, setAmount] = useState(1);
  const [reason, setReason] = useState('bonus');
  const [note, setNote] = useState('');
  const [mirror, setMirror] = useState(true);
  const [outcome, setOutcome] = useState<StarMirrorOutcome | null>(null);

  const target = userId && userId !== 'all' ? userId : undefined;
  const { data, isLoading } = useQuery({
    queryKey: ['stars', userId ?? 'me'],
    queryFn: () => api.getStars(userId),
  });

  const grant = useMutation({
    mutationFn: async (): Promise<StarGrant | AdminStarGrantResponse> =>
      admin && target
        ? api.grantStarsForUser(target, {
            stars: amount,
            reason,
            note: note.trim() || undefined,
            mirror_to_skylight: mirror,
          })
        : api.grantStars({
            user_id: target || '',
            stars: amount,
            reason,
            note: note.trim() || undefined,
          }),
    onSuccess: (result) => {
      setNote('');
      queryClient.invalidateQueries({ queryKey: ['stars'] });
      const count = `${amount} star${amount === 1 ? '' : 's'}`;
      const mirrored = 'skylight' in result ? result.skylight : null;
      if (mirrored) {
        // Kept on screen as well as toasted: a toast is gone in four seconds and
        // "the mirror did not land" is the one thing an admin must act on.
        setOutcome(mirrored);
        if (mirrored.status === 'FAILURE') {
          toast.error(`Recorded ${count}, but the Skylight mirror failed`, { duration: 8000 });
          return;
        }
        if (mirrored.status === 'SKIPPED') {
          toast(`Recorded ${count} — Skylight mirror skipped`, { icon: '⚠️', duration: 6000 });
          return;
        }
      }
      toast.success(`Gave ${count}`);
    },
    onError: (error: Error) => toast.error(error.message || 'Could not grant stars'),
  });

  if (isLoading) {
    return (
      <div className="glass-panel p-4 rounded-2xl border border-white/5 animate-pulse" data-testid="stars-loading">
        <div className="h-4 w-32 rounded bg-white/10" />
      </div>
    );
  }

  return (
    <div className="glass-panel p-4 rounded-2xl border border-white/5 space-y-3" data-testid="star-balance">
      <div className="flex items-center gap-2">
        <Sparkles size={16} className="text-amber-300" />
        <span className="text-sm font-semibold text-slate-200">Bonus stars</span>
        <span className="ml-auto rounded-full border border-amber-400/40 bg-amber-400/10 px-2.5 py-0.5 text-xs font-bold text-amber-200">
          {data?.stars ?? 0}
        </span>
      </div>

      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (amount === 0) {
            toast.error('Stars cannot be zero');
            return;
          }
          setOutcome(null);
          grant.mutate();
        }}
      >
        <input
          type="number"
          value={amount}
          onChange={(event) => setAmount(Number(event.target.value))}
          aria-label="Star amount"
          min={-100}
          max={100}
          className="glass-input w-20 px-2.5 py-2 text-sm min-h-11 pointer-coarse:min-h-11"
        />
        <select
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          aria-label="Star reason"
          className="glass-input px-2.5 py-2 text-sm min-h-11 pointer-coarse:min-h-11"
        >
          {REASONS.map((r) => (
            <option key={r.id} value={r.id}>
              {r.label}
            </option>
          ))}
        </select>
        <input
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Why?"
          aria-label="Star note"
          className="glass-input flex-1 min-w-[8rem] px-2.5 py-2 text-sm min-h-11 pointer-coarse:min-h-11"
        />
        <button
          type="submit"
          disabled={grant.isPending || !target}
          className="glass-button min-h-11 px-3 py-2 text-sm disabled:opacity-50"
          title={!target ? 'Pick one person to give stars to' : undefined}
        >
          {grant.isPending ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />} Grant
        </button>
      </form>

      {/* Admin only. The mirror is opt-out rather than implicit so an award
          recorded twice (here and in Skylight) is a deliberate choice, and the
          default stays the behaviour the rest of the app already relies on. */}
      {admin && target && (
        <label className="flex items-center gap-2 min-h-11 pointer-coarse:min-h-11 text-[11px] text-slate-400 cursor-pointer">
          <input
            type="checkbox"
            checked={mirror}
            onChange={(event) => setMirror(event.target.checked)}
            className="h-4 w-4 accent-purple-500"
          />
          Mirror into {target}&rsquo;s Skylight account
        </label>
      )}

      {outcome && (
        <p
          data-testid="star-mirror-outcome"
          data-status={outcome.status}
          className={
            outcome.status === 'SUCCESS'
              ? 'text-[11px] text-emerald-300'
              : outcome.status === 'SKIPPED'
                ? 'text-[11px] text-amber-300'
                : 'text-[11px] text-red-300'
          }
        >
          {outcome.status === 'SUCCESS'
            ? `Recorded and mirrored to Skylight.${outcome.message ? ` ${outcome.message}` : ''}`
            : outcome.status === 'SKIPPED'
              ? 'Recorded in the ledger; not mirrored to Skylight.'
              : `Recorded in the ledger, but the Skylight mirror failed${
                  outcome.message ? `: ${outcome.message}` : '.'
                } Retrying the mirror is safe — do not grant again.`}
        </p>
      )}

      {data && data.grants.length > 0 && (
        <ul className="space-y-1 text-[11px] text-slate-400" data-testid="star-ledger">
          {data.grants.slice(0, 5).map((grant_, i) => (
            <li key={`${grant_.at}-${i}`} className="truncate">
              <span className={grant_.stars >= 0 ? 'text-emerald-300' : 'text-rose-300'}>
                {grant_.stars >= 0 ? `+${grant_.stars}` : grant_.stars}
              </span>{' '}
              · {grant_.reason}
              {grant_.note ? ` · ${grant_.note}` : ''} · {grant_.granted_by}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
