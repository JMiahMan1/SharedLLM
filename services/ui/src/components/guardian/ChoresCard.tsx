import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { CheckCircle2, Circle, ListChecks, Repeat, Trash2 } from 'lucide-react';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { canManageChores, clockLabel, refreshChores } from '../../lib/chores';
import { useChoreRemoval } from '../../hooks/useChoreRemoval';
import MemberCard from '../member/MemberCard';
import AddChoreForm from '../chores/AddChoreForm';
import type { ChoreItem } from '../../types/widget';

/**
 * A child's Skylight chores for today, and adding or removing them
 * (docs/ACHIEVEMENTS.md, "Managing chores"), on the Guardian page and the
 * member page's Time section. Only for the parents who run the chore chart
 * (the gateway refuses anyone else); nothing at all when Skylight is not set
 * up. Chores live in Skylight, so they show on the family's Skylight screen
 * and the dashboard's chores widget too.
 *
 * Chores the child ticked off wait here for a parent's word when earned time
 * is on (docs/HEALTH_HUB.md, "Earning time and chores first"): "Done right" earns the minutes,
 * "Redo it" unticks the chore so it is back on the list.
 */
function localDay(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export default function ChoresCard({ username, name }: { username: string; name: string }) {
  const manager = canManageChores(useAuth().user);
  const { ask, prompt, busy, pendingId } = useChoreRemoval();

  const chores = useQuery({
    queryKey: ['skylight-chores', 'manage', username],
    queryFn: async () => {
      const resp = await api.getSkylightChores('today', username);
      if (resp.status !== 'SUCCESS') throw new Error(resp.message || 'Skylight did not answer');
      return (resp.chores ?? []) as ChoreItem[];
    },
    enabled: manager,
    retry: false,
    staleTime: 60_000,
  });

  const queryClient = useQueryClient();
  const checks = useQuery({
    queryKey: ['chore-checks', 'pending'],
    queryFn: () => api.getChoreChecks('pending'),
    enabled: manager,
    retry: false,
    refetchInterval: 60_000,
  });
  const waiting = (checks.data?.checks ?? []).filter((c) => c.username === username);
  const decide = useMutation({
    mutationFn: ({ id, approve }: { id: number; approve: boolean }) => api.decideChoreCheck(id, approve),
    onSuccess: (resp, { approve }) => {
      const title = resp.check?.title || 'The chore';
      toast.success(approve ? `"${title}" approved` : `"${title}" is back on ${name}'s list`);
      void queryClient.invalidateQueries({ queryKey: ['chore-checks'] });
      void queryClient.invalidateQueries({ queryKey: ['screen-time', username] });
      refreshChores(queryClient);
    },
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'That did not save'),
  });

  if (!manager) return null;
  // No Skylight in this house: no card, rather than an error about it.
  if (chores.error instanceof Error && /not configured|disabled/i.test(chores.error.message)) return null;

  const items = chores.data ?? [];
  const today = localDay();
  const left = items.filter((c) => !c.completed).length;

  return (
    <MemberCard
      title={`${name}'s chores`}
      icon={ListChecks}
      tone="text-emerald-300"
      testId="guardian-chores"
      aside={waiting.length ? `${waiting.length} to check` : items.length ? (left ? `${left} of ${items.length} left today` : 'All done today') : undefined}
      isLoading={chores.isLoading}
      error={chores.isError ? 'Skylight chores could not be loaded right now.' : null}
    >
      {waiting.length > 0 && (
        <div className="space-y-1.5 rounded-xl border border-sky-400/30 bg-sky-400/5 p-3" data-testid="chore-checks">
          <p className="text-xs font-semibold text-sky-200">Done right? {name} ticked {waiting.length === 1 ? 'this' : 'these'} off</p>
          <ul className="space-y-1.5">
            {waiting.map((check) => (
              <li key={check.id} className="flex flex-wrap items-center gap-2" data-testid="chore-check">
                <span className="min-w-0 flex-1 truncate text-sm text-slate-100">
                  {check.title || 'A chore'}
                  {check.day !== today && (
                    <span className="ml-1 text-[11px] text-slate-400">
                      {new Date(`${check.day}T12:00:00`).toLocaleDateString(undefined, { weekday: 'short' })}
                    </span>
                  )}
                </span>
                <button type="button" disabled={decide.isPending}
                  onClick={() => decide.mutate({ id: check.id, approve: true })}
                  className="min-h-11 rounded-xl bg-emerald-500/20 px-3 text-xs font-semibold text-emerald-200 disabled:opacity-50"
                  aria-label={`${check.title} was done right`}>
                  Done right
                </button>
                <button type="button" disabled={decide.isPending}
                  onClick={() => decide.mutate({ id: check.id, approve: false })}
                  className="glass-button min-h-11 px-3 text-xs disabled:opacity-50"
                  aria-label={`Ask ${name} to redo ${check.title}`}>
                  Redo it
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {items.length === 0 ? (
        <p className="text-xs text-slate-400" data-testid="guardian-chores-empty">No chores for {name} today. Add one below.</p>
      ) : (
        <ul className="space-y-1.5">
          {items.map((chore) => (
            <li key={chore.id} data-testid="guardian-chore"
              className={`flex min-h-11 items-center gap-2 rounded-xl px-3 ${pendingId === chore.id ? 'bg-rose-400/10' : 'bg-white/5'}`}>
              {chore.completed ? (
                <CheckCircle2 size={16} className="shrink-0 text-emerald-300" aria-label="Done" />
              ) : (
                <Circle size={16} className="shrink-0 text-slate-500" aria-label="Not done yet" />
              )}
              <span className={`min-w-0 flex-1 truncate text-sm ${chore.completed ? 'text-slate-400 line-through' : 'text-slate-100'}`}>
                {chore.emoji_icon ? `${chore.emoji_icon} ` : ''}{chore.title}
              </span>
              {chore.reward ? <span className="shrink-0 text-[11px] font-semibold text-amber-300">★ {chore.reward}</span> : null}
              {chore.recurrence && <Repeat size={13} className="shrink-0 text-slate-500" aria-label="Repeats" />}
              {chore.start_time && <span className="shrink-0 text-[11px] text-slate-400">{clockLabel(chore.start_time)}</span>}
              <button
                type="button"
                onClick={() => ask(chore)}
                disabled={busy}
                aria-label={`Remove ${chore.title}`}
                className="-mr-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-400 hover:bg-white/10 hover:text-rose-300 disabled:opacity-50"
              >
                <Trash2 size={15} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {prompt}
      <div className="border-t border-white/5 pt-3">
        <AddChoreForm person={username} personName={name} />
      </div>
    </MemberCard>
  );
}
