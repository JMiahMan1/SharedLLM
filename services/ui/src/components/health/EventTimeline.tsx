import { useQuery } from '@tanstack/react-query';
import {
  Dumbbell,
  Route,
  Trophy,
  Target,
  Zap,
  type LucideIcon,
} from 'lucide-react';
import { api } from '../../services/api';
import type { TimelineEvent } from '../../types/api';

interface Props {
  userId?: string;
  days?: number;
}

/**
 * Maps the server's `icon` string onto a component. The server sends the name
 * so the vocabulary lives in one place, but an unknown name must not blank the
 * row — hence the fallback rather than a lookup that can return undefined.
 */
const ICONS: Record<string, LucideIcon> = {
  dumbbell: Dumbbell,
  route: Route,
  trophy: Trophy,
  target: Target,
  zap: Zap,
};

const ACCENTS: Record<string, string> = {
  workout: 'text-emerald-300',
  drive: 'text-sky-300',
  achievement: 'text-amber-300',
  goal: 'text-purple-300',
  personal_best: 'text-rose-300',
};

function EventRow({ event }: { event: TimelineEvent }) {
  const Icon = ICONS[event.icon] ?? Target;
  return (
    <li
      data-testid="timeline-event"
      data-kind={event.kind}
      className="flex items-start gap-2.5 py-2"
    >
      <span
        aria-hidden
        className={`mt-0.5 shrink-0 ${ACCENTS[event.kind] ?? 'text-slate-400'}`}
      >
        <Icon size={15} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-xs font-medium text-slate-100">{event.title}</span>
        {event.detail && (
          <span className="block text-[11px] text-slate-400">{event.detail}</span>
        )}
      </span>
      {/* The server computes the label so a future-dated or skewed clock
          cannot render as a negative age here. */}
      <span className="shrink-0 text-[10px] text-slate-500 font-mono">{event.label}</span>
    </li>
  );
}

/**
 * The caller's own event timeline, grouped by day.
 *
 * Steps are not events and are deliberately absent — a day bucket is a
 * measurement, and the bar chart covers that. What is here is something that
 * happened: a workout, a drive, a badge earned.
 */
export default function EventTimeline({ userId, days = 30 }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: ['timeline', userId ?? 'me', days],
    queryFn: () => api.getTimeline(userId, days),
    staleTime: 60_000,
  });

  return (
    <section
      data-testid="event-timeline"
      aria-label="Activity timeline"
      className="lg:col-span-3 glass-panel rounded-2xl p-4"
    >
      <div className="flex items-center justify-between px-1">
        <h2 className="text-sm font-semibold text-slate-300">Timeline</h2>
        {data && !data.empty && (
          <span className="text-[10px] text-slate-500 font-mono">
            {data.total_events} event{data.total_events === 1 ? '' : 's'} · last{' '}
            {data.window_days} days
          </span>
        )}
      </div>

      {isLoading && (
        <p className="px-1 pt-3 text-xs text-slate-500">Loading your timeline…</p>
      )}

      {data?.empty && (
        <p data-testid="timeline-empty" className="px-1 pt-3 text-xs text-slate-500">
          Nothing recorded in the last {data.window_days} days. Workouts and badges you
          earn will show up here.
        </p>
      )}

      {data && !data.empty && (
        <div className="mt-1">
          {data.groups.map((group) => (
            <div key={group.day} data-testid="timeline-day" data-day={group.day}>
              <p className="sticky top-0 z-[1] bg-slate-900/80 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500 backdrop-blur">
                {group.relative}
              </p>
              <ul className="divide-y divide-white/5">
                {group.events.map((event, i) => (
                  <EventRow key={`${event.kind}-${event.at}-${i}`} event={event} />
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
