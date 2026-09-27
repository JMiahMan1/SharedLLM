import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Users } from 'lucide-react';
import { api } from '../../services/api';
import type { ActivityWindow, SharedActivityUser } from '../../types/api';

const WINDOWS: Array<{ id: ActivityWindow; label: string }> = [
  { id: 'today', label: 'Today' },
  { id: 'week', label: 'Week' },
  { id: 'month', label: 'Month' },
];

/** '4.2 mi' / '12 mi' — null when the scope was not shared. */
function formatMiles(value?: number | null): string | null {
  if (value === undefined || value === null) return null;
  return `${value >= 10 ? Math.round(value) : value.toFixed(1)} mi`;
}

type FeedPerson = SharedActivityUser & { isYou?: boolean };

function PersonRow({ person }: { person: FeedPerson }) {
  const chips: string[] = [];
  if (person.steps_total !== undefined) {
    chips.push(`${Math.round(person.steps_total).toLocaleString()} steps`);
  }
  if (person.workout_count !== undefined) {
    chips.push(`${person.workout_count} ${person.workout_count === 1 ? 'workout' : 'workouts'}`);
  }
  const miles = formatMiles(person.workout_distance_miles);
  if (miles) chips.push(miles);
  if (person.points !== undefined) chips.push(`${person.points} pts`);
  if (person.achievements_earned !== undefined) {
    chips.push(`${person.achievements_earned} ${person.achievements_earned === 1 ? 'badge' : 'badges'}`);
  }

  return (
    <div
      data-testid={`feed-user-${person.username}`}
      className="flex items-center gap-2.5 py-2.5 border-b border-white/5 last:border-b-0"
    >
      <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-emerald-600 to-indigo-600 flex items-center justify-center text-xs font-bold text-white shrink-0">
        {person.username.charAt(0).toUpperCase()}
      </div>
      <div className="min-w-0">
        <p className="text-sm font-medium text-slate-200 truncate capitalize">
          {person.username}
          {person.isYou && (
            <span className="ml-1.5 text-[10px] uppercase font-bold px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
              You
            </span>
          )}
        </p>
        <p className="text-[11px] text-slate-500 truncate">
          {chips.length ? chips.join(' · ') : 'Sharing enabled — no activity recorded yet'}
        </p>
      </div>
    </div>
  );
}

/**
 * Opt-in family activity: your own totals plus everyone who shares with you.
 * Only the scopes each person chose are rendered; enforcement is server-side.
 */
export default function FamilyActivityCard() {
  const [period, setPeriod] = useState<ActivityWindow>('week');
  const summary = useQuery({
    queryKey: ['activity-summary', period],
    queryFn: () => api.getActivitySummary(period),
    retry: false,
  });
  const feed = useQuery({
    queryKey: ['activity-feed', period],
    queryFn: () => api.getActivityFeed(period),
    retry: false,
  });

  const others = [...(feed.data?.users ?? [])].sort(
    (a, b) => (b.steps_total ?? -1) - (a.steps_total ?? -1)
  );
  const you: FeedPerson | null = summary.data
    ? {
        username: summary.data.user_id || 'you',
        window: summary.data.window,
        isYou: true,
        steps_total: summary.data.steps_total,
        steps_average: summary.data.steps_average,
        steps_today: summary.data.steps_today,
        workout_count: summary.data.workout_count,
        workout_distance_miles: summary.data.workout_distance_miles,
        drive_distance_miles: summary.data.drive_distance_miles,
        points: summary.data.points,
        achievements_earned: summary.data.achievements_earned,
      }
    : null;
  const loading = (summary.isLoading || feed.isLoading) && !summary.data && !feed.data;
  const errored = summary.isError && feed.isError;

  return (
    <div data-testid="family-activity-card" className="glass-panel p-5 rounded-2xl border border-white/5">
      <div className="flex items-center justify-between gap-3 flex-wrap mb-2">
        <div className="flex items-center gap-1.5 text-[11px] text-slate-400 uppercase tracking-wider font-semibold">
          <Users size={13} className="text-emerald-400" />
          Family Activity
        </div>
        <div className="flex items-center gap-1" role="group" aria-label="Activity window">
          {WINDOWS.map((option) => (
            <button
              key={option.id}
              type="button"
              data-testid={`feed-window-${option.id}`}
              aria-pressed={period === option.id}
              onClick={() => setPeriod(option.id)}
              className={`min-h-8 px-2.5 py-1 rounded-lg text-[11px] font-semibold transition-colors ${
                period === option.id
                  ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                  : 'text-slate-400 hover:text-white border border-transparent'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <p className="text-sm text-slate-500 py-4 text-center">Loading family activity…</p>
      ) : errored ? (
        <p className="text-sm text-slate-500 py-4 text-center">Activity feed unavailable.</p>
      ) : (
        <>
          {you && <PersonRow person={you} />}
          {others.map((user) => (
            <PersonRow key={user.username} person={user} />
          ))}
          {others.length === 0 && (
            <p data-testid="feed-empty" className="text-sm text-slate-500 pt-2">
              No one else is sharing activity yet. Family members can turn on sharing in Settings →
              Activity sharing.
            </p>
          )}
          <p className="text-[10px] text-slate-600 pt-2 border-t border-white/5 mt-1">
            Opt-in only — each person controls what they share.
          </p>
        </>
      )}
    </div>
  );
}
