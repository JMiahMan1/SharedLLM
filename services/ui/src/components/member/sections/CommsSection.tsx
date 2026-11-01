import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { Bell, MessageSquare, Phone, PhoneMissed, Search } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { api } from '../../../services/api';
import MemberCard from '../MemberCard';
import { commsLine } from '../../../lib/memberPage';
import { formatDayLabel, localDay } from '../../../lib/healthHub';
import type { ActivityEvent } from '../../../types/api';

type Kind = 'all' | 'calls' | 'sms' | 'social';

const KINDS: { id: Kind; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'calls', label: 'Calls' },
  { id: 'sms', label: 'Texts' },
  { id: 'social', label: 'App messages' },
];

const RANGES = [1, 7, 30];

function iconFor(e: ActivityEvent): LucideIcon {
  if (e.feature === 'calls') return e.detail?.direction === 'missed' ? PhoneMissed : Phone;
  if (e.feature === 'sms') return MessageSquare;
  return Bell;
}

/** Who someone talks with most: calls and texts together, by contact. */
function topContacts(events: ActivityEvent[]): { who: string; calls: number; texts: number }[] {
  const by = new Map<string, { who: string; calls: number; texts: number }>();
  for (const e of events) {
    if (e.feature !== 'calls' && e.feature !== 'sms') continue;
    const who = e.item || (typeof e.detail?.number === 'string' ? e.detail.number : '') || 'Unknown';
    const row = by.get(who) ?? { who, calls: 0, texts: 0 };
    if (e.feature === 'calls') row.calls += 1;
    else row.texts += 1;
    by.set(who, row);
  }
  return [...by.values()].sort((a, b) => b.calls + b.texts - (a.calls + a.texts)).slice(0, 5);
}

/**
 * Calls, texts and app message notifications for one person, for their
 * parents only (the server refuses anyone else, an admin who is not a parent
 * included): the totals, who they talk with most, and every call and message
 * with its full text, searchable.
 */
export default function CommsSection({ username, name }: { username: string; name: string }) {
  const [days, setDays] = useState(7);
  const [kind, setKind] = useState<Kind>('all');
  const [search, setSearch] = useState('');
  const [open, setOpen] = useState<string[]>([]);
  const { data, isLoading, error } = useQuery({
    queryKey: ['member-comms', username, days],
    queryFn: () => api.getGuardianComms(username, days),
    retry: false,
    staleTime: 60_000,
  });
  const refused = isAxiosError(error) && error.response?.status === 403;
  const events = useMemo(() => data?.events ?? [], [data]);

  const totals = useMemo(() => {
    const calls = events.filter((e) => e.feature === 'calls');
    const texts = events.filter((e) => e.feature === 'sms');
    return {
      calls: calls.length,
      missed: calls.filter((e) => e.detail?.direction === 'missed').length,
      talkMin: Math.round(calls.reduce((n, e) => n + (typeof e.duration_s === 'number' ? e.duration_s : 0), 0) / 60),
      sent: texts.filter((e) => e.detail?.direction === 'out').length,
      received: texts.filter((e) => e.detail?.direction === 'in').length,
      app: events.filter((e) => e.feature === 'social').length,
    };
  }, [events]);
  const contacts = useMemo(() => topContacts(events), [events]);

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    return events.filter((e) => {
      if (kind !== 'all' && e.feature !== kind) return false;
      if (!q) return true;
      const line = commsLine(e);
      return [line?.title, line?.text, line?.number, e.item].some((s) => typeof s === 'string' && s.toLowerCase().includes(q));
    });
  }, [events, kind, search]);

  // Grouped by day, newest first (the events already are).
  const groups = useMemo(() => {
    const out: { day: string; events: ActivityEvent[] }[] = [];
    for (const e of shown) {
      const day = localDay(new Date(e.at));
      if (out.length === 0 || out[out.length - 1].day !== day) out.push({ day, events: [] });
      out[out.length - 1].events.push(e);
    }
    return out;
  }, [shown]);

  const chip = (on: boolean) =>
    `min-h-11 shrink-0 rounded-full px-3 text-xs font-medium ${on ? 'bg-[var(--site-accent-soft)] text-[var(--site-accent-text)]' : 'bg-white/5 text-slate-300 hover:bg-white/10'}`;

  if (refused || error || isLoading) {
    return (
      <MemberCard
        title="Calls & messages"
        icon={MessageSquare}
        testId="member-comms"
        wide
        isLoading={isLoading}
        error={refused ? 'Only a parent can see calls and messages.' : error ? 'Calls and messages could not be loaded.' : null}
      />
    );
  }
  const today = localDay();
  return (
    <>
      <MemberCard
        title={`${name}'s calls & messages`}
        icon={MessageSquare}
        testId="member-comms-summary"
        wide
        aside={
          <span className="flex gap-1" role="group" aria-label="Period">
            {RANGES.map((d) => (
              <button key={d} type="button" aria-pressed={days === d} onClick={() => setDays(d)} className={chip(days === d)}>
                {d === 1 ? 'Today' : `${d} days`}
              </button>
            ))}
          </span>
        }
      >
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 text-xs" data-testid="member-comms-totals">
          <Stat label="Calls" value={String(totals.calls)} hint={totals.missed ? `${totals.missed} missed` : undefined} />
          <Stat label="Talk time" value={`${totals.talkMin} min`} />
          <Stat label="Texts" value={String(totals.sent + totals.received)} hint={`${totals.sent} sent · ${totals.received} received`} />
          <Stat label="App messages" value={String(totals.app)} />
        </div>
        {contacts.length > 0 && (
          <div className="space-y-1.5">
            <p className="text-[10px] uppercase tracking-wider text-slate-500">Most in touch with</p>
            <ul className="flex flex-wrap gap-1.5" data-testid="member-comms-contacts">
              {contacts.map((c) => (
                <li key={c.who}>
                  <button type="button" onClick={() => setSearch(c.who)} className="min-h-9 rounded-full bg-white/5 px-3 text-xs text-slate-200 hover:bg-white/10">
                    {c.who} <span className="text-slate-500">· {c.calls ? `${c.calls} call${c.calls === 1 ? '' : 's'}` : ''}{c.calls && c.texts ? ', ' : ''}{c.texts ? `${c.texts} text${c.texts === 1 ? '' : 's'}` : ''}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </MemberCard>

      <MemberCard
        title="Every call and message"
        icon={Phone}
        testId="member-comms"
        wide
        empty={events.length === 0 ? `No calls or messages from ${name}'s phone ${days === 1 ? 'today' : `in the last ${days} days`}. They show up once the phone allows calls and texts (Settings > Phone reports) and runs a recent app.` : null}
      >
        <div className="flex flex-wrap items-center gap-2">
          <div className="-mx-1 flex gap-2 overflow-x-auto px-1" role="group" aria-label="Show">
            {KINDS.map((k) => (
              <button key={k.id} type="button" aria-pressed={kind === k.id} onClick={() => setKind(k.id)} className={chip(kind === k.id)}>
                {k.label}
              </button>
            ))}
          </div>
          <label className="flex min-w-0 flex-1 items-center gap-2 rounded-xl bg-white/5 px-3">
            <Search size={14} className="shrink-0 text-slate-500" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search names, numbers and messages"
              aria-label="Search calls and messages"
              className="min-h-11 w-full min-w-0 bg-transparent text-base text-slate-200 outline-none sm:text-xs"
            />
          </label>
        </div>
        {shown.length === 0 ? (
          <p className="text-xs text-slate-400" data-testid="member-comms-none">Nothing matches.</p>
        ) : (
          <div className="space-y-3" data-testid="member-comms-list">
            {groups.map((g) => (
              <section key={g.day} className="space-y-1">
                <h4 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{g.day === today ? 'Today' : formatDayLabel(g.day)}</h4>
                <ol className="space-y-1">
                  {g.events.map((e, i) => {
                    const line = commsLine(e);
                    const Icon = iconFor(e);
                    const key = `${e.at}-${i}`;
                    const expanded = open.includes(key);
                    return (
                      <li key={key} className="rounded-xl bg-white/5 px-3 py-2 text-xs" data-testid="member-comms-item">
                        <div className="flex items-start gap-2">
                          <Icon size={14} className={`mt-0.5 shrink-0 ${e.detail?.direction === 'missed' ? 'text-rose-300' : 'text-slate-400'}`} />
                          <div className="min-w-0 flex-1">
                            <p className="flex flex-wrap items-baseline gap-x-2 text-slate-200">
                              <span className="break-words">{line?.title ?? e.item}</span>
                              {line?.length && <span className="text-slate-400">{line.length}</span>}
                              {line?.number && <span className="text-slate-500">{line.number}</span>}
                              <span className="ml-auto shrink-0 text-[10px] text-slate-500">
                                {new Date(e.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
                              </span>
                            </p>
                            {line?.text && (
                              <button
                                type="button"
                                onClick={() => setOpen((list) => (expanded ? list.filter((k) => k !== key) : [...list, key]))}
                                aria-expanded={expanded}
                                className={`mt-1 block w-full whitespace-pre-wrap break-words text-left text-slate-300 ${expanded ? '' : 'line-clamp-3'}`}
                              >
                                {line.text}
                              </button>
                            )}
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ol>
              </section>
            ))}
          </div>
        )}
      </MemberCard>
    </>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-0 rounded-xl bg-white/5 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider text-slate-500">{label}</p>
      <p className="truncate text-sm font-semibold text-white">{value}</p>
      {hint && <p className="truncate text-[10px] text-slate-400">{hint}</p>}
    </div>
  );
}
