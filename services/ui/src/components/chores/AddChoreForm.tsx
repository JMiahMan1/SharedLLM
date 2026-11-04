import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import toast from 'react-hot-toast';
import { api, type NewChore } from '../../services/api';
import { CHORE_REPEATS, refreshChores } from '../../lib/chores';

/**
 * Add a Skylight chore (docs/ACHIEVEMENTS.md, "Managing chores"): its name,
 * who it is for, how often, a time and stars. With `person` it is for that
 * one child (the Guardian and member cards); with `people` the parent picks
 * one or more from the chore chart (the dashboard).
 */
export default function AddChoreForm({ person, personName, people, onAdded, autoFocus = false }: {
  /** The one person it is for (a login name or Skylight label). */
  person?: string;
  personName?: string;
  /** Or: everyone on the chore chart, to choose from. */
  people?: Array<{ value: string; label: string; color?: string | null }>;
  onAdded?: () => void;
  autoFocus?: boolean;
}) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState('');
  const [chosen, setChosen] = useState<string[]>([]);
  const [repeat, setRepeat] = useState('once');
  const [time, setTime] = useState('');
  const [stars, setStars] = useState('');

  const add = useMutation({
    mutationFn: (chore: NewChore) => api.addSkylightChore(chore),
    onSuccess: (_d, chore) => {
      const who = person ? personName || person : chore.assignees.join(' and ');
      toast.success(`Added "${chore.title}" for ${who}`);
      setTitle('');
      setTime('');
      setStars('');
      setRepeat('once');
      setChosen([]);
      refreshChores(queryClient);
      onAdded?.();
    },
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'Could not add the chore'),
  });

  const submit = () => {
    const name = title.trim();
    if (!name) {
      toast.error('Give the chore a name');
      return;
    }
    const assignees = person ? [person] : chosen;
    if (!assignees.length) {
      toast.error('Choose who the chore is for');
      return;
    }
    const count = stars.trim() ? Number(stars) : undefined;
    if (count !== undefined && (!Number.isInteger(count) || count < 0 || count > 100)) {
      toast.error('Stars are a whole number from 0 to 100');
      return;
    }
    add.mutate({
      title: name,
      assignees,
      ...(time ? { time } : {}),
      ...(repeat !== 'once' ? { repeat } : {}),
      ...(count !== undefined ? { stars: count } : {}),
    });
  };

  const toggle = (value: string) =>
    setChosen((now) => (now.includes(value) ? now.filter((v) => v !== value) : [...now, value]));

  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      aria-label={person ? `Add a chore for ${personName || person}` : 'Add a chore'}
      data-testid="add-chore-form"
    >
      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder={person ? `New chore for ${personName || person}` : 'e.g. Feed the chickens'}
        aria-label="Chore name"
        maxLength={120}
        autoFocus={autoFocus}
        className="glass-input min-h-11 w-full bg-black/30"
      />
      {!person && people && (
        <div role="group" aria-label="Who is it for" className="flex flex-wrap gap-2">
          {people.length === 0 && <p className="text-xs text-slate-400">No one is on the Skylight chore chart yet.</p>}
          {people.map((p) => {
            const on = chosen.includes(p.value);
            return (
              <button
                key={p.value}
                type="button"
                aria-pressed={on}
                onClick={() => toggle(p.value)}
                className={`flex min-h-11 items-center gap-2 rounded-xl border px-3 text-sm transition-colors ${
                  on ? 'border-[var(--site-accent-border)] bg-[var(--site-accent-soft)] text-[var(--site-accent-text)]' : 'border-white/10 bg-white/5 text-slate-300'
                }`}
              >
                {p.color && <span className="h-2.5 w-2.5 rounded-full" style={{ background: p.color }} aria-hidden />}
                {p.label}
              </button>
            );
          })}
        </div>
      )}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-[1fr_1fr_5rem]">
        <select value={repeat} onChange={(e) => setRepeat(e.target.value)} aria-label="How often"
          className="glass-input min-h-11 min-w-0 bg-black/30">
          {CHORE_REPEATS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <input type="time" value={time} onChange={(e) => setTime(e.target.value)} aria-label="Time (optional)"
          className="glass-input min-h-11 min-w-0 bg-black/30" />
        <input value={stars} onChange={(e) => setStars(e.target.value)} inputMode="numeric" placeholder="Stars"
          aria-label="Stars (optional)" className="glass-input col-span-2 min-h-11 min-w-0 bg-black/30 sm:col-span-1" />
      </div>
      <button type="submit" disabled={add.isPending}
        className="flex min-h-11 w-full items-center justify-center gap-1.5 rounded-xl border border-[var(--site-accent-border)] bg-[var(--site-accent-soft)] text-sm font-medium text-[var(--site-accent-text)] disabled:opacity-50">
        <Plus size={15} /> {add.isPending ? 'Adding…' : 'Add chore'}
      </button>
    </form>
  );
}
