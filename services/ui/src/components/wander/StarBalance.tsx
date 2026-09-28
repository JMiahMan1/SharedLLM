import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Sparkles, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';

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
 */
export default function StarBalance({ userId }: { userId?: string }) {
  const queryClient = useQueryClient();
  const [amount, setAmount] = useState(1);
  const [reason, setReason] = useState('bonus');
  const [note, setNote] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['stars', userId ?? 'me'],
    queryFn: () => api.getStars(userId),
  });

  const grant = useMutation({
    mutationFn: () =>
      api.grantStars({
        user_id: userId && userId !== 'all' ? userId : '',
        stars: amount,
        reason,
        note: note.trim() || undefined,
      }),
    onSuccess: () => {
      setNote('');
      queryClient.invalidateQueries({ queryKey: ['stars'] });
      toast.success(`Gave ${amount} star${amount === 1 ? '' : 's'}`);
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
          className="glass-input w-20 px-2.5 py-2 text-sm"
        />
        <select
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          aria-label="Star reason"
          className="glass-input px-2.5 py-2 text-sm"
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
          className="glass-input flex-1 min-w-[8rem] px-2.5 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={grant.isPending || !userId || userId === 'all'}
          className="glass-button min-h-11 px-3 py-2 text-sm disabled:opacity-50"
          title={!userId || userId === 'all' ? 'Pick one person to give stars to' : undefined}
        >
          {grant.isPending ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />} Grant
        </button>
      </form>

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
