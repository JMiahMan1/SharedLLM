import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Activity, BookOpen, ChevronLeft, ChevronRight, Gamepad2, Globe, ListOrdered, MessageSquare, Music, Phone, Smartphone } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import MemberCard from '../MemberCard';
import { ACTIVITY_KINDS, ACTIVITY_LIMIT, activityQuery, kindOf, kindTotals } from '../../../lib/memberPage';
import { formatDayLabel, localDay, shiftDay } from '../../../lib/healthHub';

const ICONS: Record<string, LucideIcon> = {
  media: Music, music: Music, video: Music, podcast: Music, audiobook: Music,
  bible: BookOpen, trivia: Gamepad2, game: Gamepad2, web: Globe, sms: MessageSquare, calls: Phone, page: Smartphone,
};
const WINDOWS = [7, 15, 30];
const chip = (on: boolean) =>
  `min-h-11 shrink-0 rounded-xl border px-3 text-xs ${on ? 'border-sky-400/40 bg-sky-400/10 text-white' : 'border-white/10 bg-white/5 text-slate-400'}`;

/** Qustodio's timeline: one day at a time, filterable by kind, newest first. */
function TimelineCard({ username, name }: { username: string; name: string }) {
  const { data, isLoading, isError } = useQuery(activityQuery(username));
  const today = localDay();
  const [day, setDay] = useState(today);
  const [kinds, setKinds] = useState<string[]>([]);
  const events = (data?.events || []).filter((e) => {
    const at = new Date(e.at);
    return !Number.isNaN(at.getTime()) && localDay(at) === day && (kinds.length === 0 || kinds.includes(kindOf(e.feature)));
  });
  const toggle = (id: string) => setKinds(kinds.includes(id) ? kinds.filter((k) => k !== id) : [...kinds, id]);

  return (
    <MemberCard title={`What ${name} did`} icon={Activity} tone="text-emerald-300" testId="member-timeline" wide>
      <div className="flex items-center justify-between gap-2">
        <button type="button" className={chip(false)} aria-label="The day before" onClick={() => setDay(shiftDay(day, -1))}>
          <ChevronLeft size={14} />
        </button>
        <span className="text-xs text-slate-200" data-testid="member-timeline-day">{day === today ? 'Today' : formatDayLabel(day)}</span>
        <button type="button" className={chip(false)} aria-label="The day after" disabled={day >= today} onClick={() => setDay(shiftDay(day, 1))}>
          <ChevronRight size={14} />
        </button>
      </div>
      <div className="-mx-1 flex gap-2 overflow-x-auto px-1" role="group" aria-label="Show only">
        {ACTIVITY_KINDS.map((k) => (
          <button key={k.id} type="button" aria-pressed={kinds.includes(k.id)} className={chip(kinds.includes(k.id))} onClick={() => toggle(k.id)}>
            {k.label}
          </button>
        ))}
      </div>
      {isLoading ? (
        <div className="h-24 skeleton rounded-xl" aria-hidden />
      ) : isError ? (
        <p className="text-xs text-amber-300">Activity could not be loaded.</p>
      ) : events.length === 0 ? (
        <p className="text-xs text-slate-400" data-testid="member-timeline-empty">Nothing recorded {day === today ? 'today' : 'that day'}{kinds.length ? ' of that kind' : ''}.</p>
      ) : (
        <ol className="relative space-y-0 border-l border-white/10 pl-4" data-testid="member-timeline-list">
          {events.map((e, i) => {
            const Icon = ICONS[e.feature] || Activity;
            const body = e.feature === 'sms' && typeof e.detail?.body === 'string' ? e.detail.body : null;
            return (
              <li key={`${e.at}-${i}`} className="relative py-1.5 text-xs">
                <span className="absolute -left-[1.4rem] top-2 flex h-4 w-4 items-center justify-center rounded-full bg-slate-800 ring-1 ring-white/10">
                  <Icon size={10} className="text-slate-300" />
                </span>
                <span className="flex items-baseline gap-2">
                  <span className="shrink-0 text-[10px] text-slate-500">{new Date(e.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</span>
                  <span className="min-w-0 truncate text-slate-200">
                    {e.item || e.feature}
                    {e.action && e.action !== 'view' ? <span className="text-slate-500"> · {e.action}</span> : null}
                  </span>
                </span>
                {body && <span className="block pl-12 text-slate-400 line-clamp-2">{body}</span>}
              </li>
            );
          })}
        </ol>
      )}
    </MemberCard>
  );
}

/** How much of each kind over 7, 15 or 30 days. */
function TotalsCard({ username }: { username: string }) {
  const { data, isLoading, isError } = useQuery(activityQuery(username));
  const [days, setDays] = useState(7);
  const events = data?.events || [];
  const totals = kindTotals(events, days);
  const rows = [...ACTIVITY_KINDS.map((k) => ({ id: k.id, label: k.label })), { id: 'other', label: 'Other' }].filter((k) => totals[k.id]);
  const top = Math.max(1, ...rows.map((r) => totals[r.id]));
  const oldest = events.length ? localDay(new Date(events[events.length - 1].at)) : null;
  const short = events.length >= ACTIVITY_LIMIT && oldest && oldest > shiftDay(localDay(), -(days - 1));
  return (
    <MemberCard
      title="Totals"
      icon={ListOrdered}
      testId="member-activity-totals"
      isLoading={isLoading}
      error={isError ? 'Activity could not be loaded.' : null}
      aside={
        <span className="flex gap-1" role="group" aria-label="Over the last">
          {WINDOWS.map((w) => (
            <button key={w} type="button" aria-pressed={days === w} className={chip(days === w)} onClick={() => setDays(w)}>
              {w} d
            </button>
          ))}
        </span>
      }
    >
      {rows.length === 0 ? (
        <p className="text-xs text-slate-400">Nothing recorded in the last {days} days.</p>
      ) : (
        <ul className="space-y-2">
          {rows.map((r) => (
            <li key={r.id} className="space-y-1 text-xs">
              <div className="flex justify-between">
                <span className="text-slate-200">{r.label}</span>
                <span className="text-slate-400">{totals[r.id]}</span>
              </div>
              <div className="h-1.5 rounded-full bg-white/5">
                <div className="h-1.5 rounded-full bg-sky-400" style={{ width: `${(totals[r.id] / top) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}
      {short && <p className="text-[11px] text-slate-500">The record keeps the latest {ACTIVITY_LIMIT} things, back to {formatDayLabel(oldest)}.</p>}
    </MemberCard>
  );
}

export default function ActivitySection({ username, name }: { username: string; name: string }) {
  return (
    <>
      <TimelineCard username={username} name={name} />
      <TotalsCard username={username} />
    </>
  );
}
