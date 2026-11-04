import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import { refreshChores } from '../lib/chores';
import type { ChoreItem } from '../types/widget';

/**
 * Removing a Skylight chore, the same way everywhere: a one-off goes at once;
 * a repeating chore asks "just today, or every day?" first.
 *
 * `ask(chore)` starts it; render `prompt` where the question should appear
 * (it is null until a repeating chore is asked about).
 */
export function useChoreRemoval() {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<ChoreItem | null>(null);

  const remove = useMutation({
    mutationFn: ({ chore, applyTo }: { chore: ChoreItem; applyTo?: 'one' | 'all' }) => api.removeSkylightChore(chore.id, applyTo),
    onSuccess: (_d, { chore, applyTo }) => {
      toast.success(applyTo === 'all' ? `Removed "${chore.title}" for good` : `Removed "${chore.title}"${applyTo ? ' for today' : ''}`);
      setPending(null);
      refreshChores(queryClient);
    },
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'Could not remove the chore'),
  });

  const ask = (chore: ChoreItem) => (chore.recurrence ? setPending(chore) : remove.mutate({ chore }));
  const busy = remove.isPending;

  const prompt = pending ? (
    <div className="space-y-2 rounded-xl border border-rose-400/30 bg-rose-400/5 p-3" role="group"
      aria-label={`Remove ${pending.title}`} data-testid="chore-remove-prompt">
      <p className="text-xs text-slate-200">"{pending.title}" repeats. Remove just today's, or every day of it?</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="glass-button min-h-11 px-3 text-xs" disabled={busy}
          onClick={() => remove.mutate({ chore: pending, applyTo: 'one' })}>Just today</button>
        <button type="button" className="min-h-11 rounded-xl bg-rose-500/20 px-3 text-xs font-semibold text-rose-200" disabled={busy}
          onClick={() => remove.mutate({ chore: pending, applyTo: 'all' })}>Every day</button>
        <button type="button" className="min-h-11 px-3 text-xs text-slate-400" onClick={() => setPending(null)}>Keep it</button>
      </div>
    </div>
  ) : null;

  return { ask, prompt, busy, pendingId: pending?.id ?? null, cancel: () => setPending(null) };
}
