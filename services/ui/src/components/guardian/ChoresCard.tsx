import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, Circle, ListChecks, Repeat, Trash2 } from 'lucide-react';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { canManageChores, clockLabel } from '../../lib/chores';
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
 */
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

  if (!manager) return null;
  // No Skylight in this house: no card, rather than an error about it.
  if (chores.error instanceof Error && /not configured|disabled/i.test(chores.error.message)) return null;

  const items = chores.data ?? [];
  const left = items.filter((c) => !c.completed).length;

  return (
    <MemberCard
      title={`${name}'s chores`}
      icon={ListChecks}
      tone="text-emerald-300"
      testId="guardian-chores"
      aside={items.length ? (left ? `${left} of ${items.length} left today` : 'All done today') : undefined}
      isLoading={chores.isLoading}
      error={chores.isError ? 'Skylight chores could not be loaded right now.' : null}
    >
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
