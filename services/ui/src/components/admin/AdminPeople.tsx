import { useMemo, useState, type ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity, AlertTriangle, Bug, ChevronRight, Lightbulb, Mic, RefreshCw, ShieldHalf, Smartphone, Timer, Users,
} from 'lucide-react';
import { api } from '../../services/api';
import MemberCard from '../member/MemberCard';
import { DailyBars } from '../member/charts';
import UserAvatar from '../UserAvatar';
import { useFamilyMembers } from '../chat/familyDirectory';
import ProfileAreaCard from '../profile/ProfileAreaCard';
import { HEADER_ACTION } from '../ui/PageHeader';
import { ErrorRetry, CardSkeletons } from '../ui/StateViews';
import { firstName, plural } from '../../lib/profileStats';
import {
  adminPeoplePath, ago, newestVersion, percent, personFromPath, problemText,
  type AdminPersonStats, type AdminVoice, type Section,
} from '../../lib/adminPeople';
import { memberPath } from '../../lib/memberPage';

const NOT_PEOPLE = new Set(['default', 'anonymous', 'household']);

/**
 * Admin > People (docs/PROFILE_STATS.md): one person at a time, everything
 * Jarvis knows that helps fix it and look after them. Not opt-in, and the
 * server refuses anyone who is not an admin. Content stays out: assistant
 * conversations are counted, never shown, and the private chat is never read.
 */
export default function AdminPeople() {
  const location = useLocation();
  const members = useFamilyMembers();
  const people = useMemo(
    () => members.filter((m) => m.username && !NOT_PEOPLE.has(m.username.toLowerCase()))
      .sort((a, b) => (a.display_name || '').localeCompare(b.display_name || '')),
    [members],
  );
  const chosen = personFromPath(location.pathname) || people[0]?.username?.toLowerCase() || null;

  return (
    <section className="space-y-4" data-testid="admin-people">
      <div className="glass-panel space-y-3 rounded-2xl border border-white/10 p-4">
        <div className="flex items-center gap-2.5">
          <Users size={18} className="text-sky-300" aria-hidden />
          <div>
            <h2 className="text-base font-semibold text-white">People</h2>
            <p className="text-xs text-slate-400">How Jarvis is working for each person, and how they are doing.</p>
          </div>
        </div>
        <nav aria-label="People" className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1" data-testid="admin-people-strip">
          {people.map((p) => {
            const current = p.username.toLowerCase() === chosen;
            return (
              <Link key={p.username} to={adminPeoplePath(p.username)} aria-current={current ? 'page' : undefined}
                data-testid={`admin-person-${p.username}`}
                className={`flex min-h-11 shrink-0 items-center gap-2 rounded-full border py-1 pl-1 pr-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-400/70 ${
                  current ? 'border-sky-300/40 bg-sky-300/10 text-white' : 'border-white/10 text-slate-300 hover:bg-white/5'}`}>
                <UserAvatar username={p.username} avatar={p.avatar} name={p.display_name} size={32} />
                <span className="max-w-[9rem] truncate">{p.display_name || p.username}</span>
              </Link>
            );
          })}
        </nav>
      </div>
      {chosen ? <PersonStats username={chosen} /> : (
        <p className="glass-panel rounded-2xl p-5 text-sm text-slate-400">No one in the family yet.</p>
      )}
    </section>
  );
}

function PersonStats({ username }: { username: string }) {
  const queryClient = useQueryClient();
  const { data, isLoading, error, refetch, isFetching } = useQuery<AdminPersonStats>({
    queryKey: ['admin-person-stats', username],
    queryFn: () => api.getAdminPersonStats(username),
    staleTime: 30 * 1000,
    retry: false,
  });
  const refresh = async () => queryClient.setQueryData(['admin-person-stats', username], await api.getAdminPersonStats(username, true));

  if (isLoading) return <CardSkeletons count={6} label="Loading" className="grid gap-3 md:grid-cols-2" height="h-48" />;
  if (error || !data) return <ErrorRetry title="These stats could not be loaded" detail="Jarvis is not answering just now." onRetry={() => void refetch()} />;
  const name = data.person.display_name || username;
  const s = data.sections;
  const who = { username, name, own: false };

  return (
    <div className="space-y-4" data-testid="admin-person-stats">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-lg font-semibold text-white">{name}</h3>
        <Link to={memberPath(username)} className={HEADER_ACTION} data-testid="admin-open-member">
          <ShieldHalf size={14} aria-hidden />Family member page
        </Link>
        <button type="button" className={HEADER_ACTION} onClick={() => void refresh()} disabled={isFetching} data-testid="admin-people-refresh">
          <RefreshCw size={14} aria-hidden className={isFetching ? 'animate-spin' : ''} />Refresh
        </button>
      </div>

      <Flags flags={data.flags} name={name} />

      <div className="grid min-w-0 gap-3 md:grid-cols-2">
        <VoiceCard section={s.voice} />
        <DevicesCard section={s.usage} collector={s.collector} />
        <FeaturesCard section={s.usage} counts={s.features} />
        <GapsCard section={s.usage} />
        <AssistantCard section={s.usage} />
        <ScreenCard section={s.screen} username={username} />
        <GuardianCard section={s.guardian} username={username} />
        <LogsCard section={s.logs} />
      </div>

      <h3 className="px-1 pt-2 text-sm font-semibold uppercase tracking-wider text-slate-400">Across Jarvis</h3>
      <div className="grid min-w-0 gap-3 md:grid-cols-2" data-testid="admin-person-profile">
        {data.profile.cards.map((card) => <ProfileAreaCard key={card.area} card={card} who={who} />)}
      </div>
    </div>
  );
}

function Flags({ flags, name }: { flags: AdminPersonStats['flags']; name: string }) {
  if (!flags.length) {
    return (
      <p className="glass-panel rounded-2xl border border-emerald-400/20 p-4 text-sm text-emerald-200" data-testid="admin-flags-none">
        Nothing stands out for {firstName(name)} right now.
      </p>
    );
  }
  return (
    <ul className="glass-panel space-y-2 rounded-2xl border border-amber-400/25 p-4" data-testid="admin-flags" aria-label="Worth a look">
      {flags.map((f) => (
        <li key={f.text} className="flex items-start gap-2 text-sm">
          {f.level === 'fix' ? <Bug size={15} className="mt-0.5 shrink-0 text-rose-300" aria-hidden /> : <AlertTriangle size={15} className="mt-0.5 shrink-0 text-amber-300" aria-hidden />}
          <span className="text-slate-100">{f.text}</span>
        </li>
      ))}
    </ul>
  );
}

function sectionState<T>(section: Section<T>): { error: string | null; data: T | null } {
  return section.state === 'ok' ? { error: null, data: section.data } : { error: section.reason, data: null };
}

function Kv({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0 rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2">
      <p className="truncate text-[11px] text-slate-400">{label}</p>
      <p className="truncate text-base font-semibold tabular-nums text-white">{value}</p>
    </div>
  );
}

const OUTCOME_WORDS: Record<string, string> = {
  ok: 'Answered', timeout: 'Timed out', error: 'Failed', tool_failure: 'A tool failed', offline: 'Fell back: AI offline', queued: 'Waited in line',
};

const IDENTITY_WORDS: Record<string, string> = {
  identified: 'Recognised by voice', 'device-assigned': 'By their satellite', default: 'As the default user',
};

function VoiceCard({ section }: { section: Section<AdminVoice | null> }) {
  const { error, data } = sectionState(section);
  const total = data?.total ?? 0;
  const failed = data ? (data.outcomes.timeout ?? 0) + (data.outcomes.error ?? 0) + (data.outcomes.tool_failure ?? 0) + (data.outcomes.offline ?? 0) : 0;
  return (
    <MemberCard title="Requests to Jarvis" icon={Mic} tone="text-purple-300" testId="admin-voice" wide error={error}
      empty={!error && (!data || !total) ? 'No requests recorded yet.' : null}
      aside={data ? `${data.days} days` : undefined}>
      {data && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Kv label="Requests" value={total.toLocaleString()} />
            <Kv label="Did not go through" value={`${failed} (${percent(failed, total)})`} />
            <Kv label="Typical wait" value={data.latency.p50_ms != null ? `${(data.latency.p50_ms / 1000).toFixed(1)} s` : '-'} />
            <Kv label="Slowest tenth over" value={data.latency.p90_ms != null ? `${(data.latency.p90_ms / 1000).toFixed(1)} s` : '-'} />
          </div>
          <ul className="flex flex-wrap gap-1.5 text-[11px]">
            {Object.entries(data.outcomes).filter(([, n]) => n > 0).map(([k, n]) => (
              <li key={k} className={`rounded-full border px-2 py-0.5 ${k === 'ok' ? 'border-emerald-400/30 text-emerald-200' : k === 'queued' ? 'border-sky-400/30 text-sky-200' : 'border-amber-400/30 text-amber-200'}`}>
                {OUTCOME_WORDS[k] ?? k}: {n}
              </li>
            ))}
          </ul>
          {data.identity && Object.values(data.identity).some((n) => n > 0) && (
            <ul className="flex flex-wrap gap-1.5 text-[11px]" data-testid="admin-voice-identity">
              {Object.entries(data.identity).filter(([, n]) => n > 0).map(([k, n]) => (
                <li key={k} className="rounded-full border border-violet-400/30 px-2 py-0.5 text-violet-200">
                  {IDENTITY_WORDS[k] ?? k}: {n}
                </li>
              ))}
            </ul>
          )}
          {data.per_day.length > 1 && (
            <DailyBars testId="admin-voice-days" title="Requests that did not go through, by day"
              data={data.per_day.map((d) => ({ key: d.day, label: d.day.slice(5), value: d.failed }))}
              format={(v) => plural(v, 'failure')} height={120} />
          )}
          {data.tools.length > 0 && (
            <div>
              <p className="px-1 pb-1 text-[11px] font-medium uppercase tracking-wider text-slate-500">Tools that failed most</p>
              <ul className="space-y-1 text-sm">
                {data.tools.slice(0, 6).map((t) => (
                  <li key={t.tool} className="flex justify-between gap-2 rounded-lg px-2 py-1 hover:bg-white/[0.03]">
                    <span className="truncate font-mono text-xs text-slate-200">{t.tool}</span>
                    <span className="shrink-0 text-xs text-rose-200">{plural(t.failures, 'failure')}{t.calls ? ` of ${t.calls}` : ''}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {data.slow_hours.length > 0 && (
            <p className="text-xs text-slate-400">
              Slowest hours: {data.slow_hours.slice(0, 3).map((h) => `${String(h.hour).padStart(2, '0')}:00 (${h.slow} of ${h.total} slow)`).join(', ')}
            </p>
          )}
        </div>
      )}
    </MemberCard>
  );
}

function DevicesCard({ section, collector }: { section: Section<AdminPersonStats['sections']['usage'] extends Section<infer U> ? U : never>; collector: AdminPersonStats['sections']['collector'] }) {
  const { error, data } = sectionState(section);
  const health = sectionState(collector);
  const devices = (data?.devices ?? []).filter((d) => !d.archived && !d.revoked);
  const newest = newestVersion(devices);
  return (
    <MemberCard title="Devices and app" icon={Smartphone} tone="text-sky-300" testId="admin-devices" error={error}
      empty={!error && !devices.length ? 'No devices registered.' : null}>
      <ul className="space-y-2">
        {devices.map((d, i) => (
          <li key={`${d.label}-${i}`} className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2 text-sm">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-slate-100">{d.label || d.model || d.kind}</span>
              <span className={`shrink-0 text-xs ${d.app_version && newest && d.app_version !== newest ? 'text-amber-300' : 'text-slate-400'}`}>
                {d.app_version ? `app ${d.app_version}` : d.kind}
              </span>
            </div>
            <p className="truncate text-[11px] text-slate-500">
              {[d.manufacturer, d.model, d.os_version ? `OS ${d.os_version}` : null].filter(Boolean).join(' ')} . seen {ago(d.last_seen_at)}
            </p>
          </li>
        ))}
      </ul>
      {health.error ? (
        <p className="text-xs text-amber-300">Collection health: {health.error}</p>
      ) : health.data && health.data.problem_count > 0 ? (
        <div className="rounded-xl border border-rose-400/20 bg-rose-400/5 p-3" data-testid="admin-collector-problems">
          <p className="pb-1 text-xs font-semibold text-rose-200">{plural(health.data.problem_count, 'collection problem')}</p>
          <ul className="space-y-0.5 text-xs text-slate-300">
            {health.data.devices.flatMap((d) => d.problems.map((p, i) => (
              <li key={`${d.device_name}-${i}`}>{d.device_name ? `${d.device_name}: ` : ''}{problemText(p)}</li>
            )))}
          </ul>
        </div>
      ) : health.data && health.data.devices.length ? (
        <p className="text-xs text-emerald-300">Phones are reporting and collecting normally.</p>
      ) : null}
    </MemberCard>
  );
}

function Bars({ rows }: { rows: Array<{ label: string; count: number }> }) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <ul className="space-y-1.5">
      {rows.map((r) => (
        <li key={r.label} className="text-xs">
          <div className="flex justify-between gap-2"><span className="truncate text-slate-200">{r.label}</span><span className="tabular-nums text-slate-400">{r.count}</span></div>
          <div className="mt-0.5 h-1.5 rounded-full bg-white/5" aria-hidden><div className="h-full rounded-full bg-sky-400/70" style={{ width: `${(r.count / max) * 100}%` }} /></div>
        </li>
      ))}
    </ul>
  );
}

type UsageSection = AdminPersonStats['sections']['usage'];

function FeaturesCard({ section, counts }: { section: UsageSection; counts: AdminPersonStats['sections']['features'] }) {
  const usage = sectionState(section);
  const recent = sectionState(counts);
  const rows = recent.data ? Object.entries(recent.data.counts).map(([label, count]) => ({ label, count })).sort((a, b) => b.count - a.count).slice(0, 8) : [];
  const allTime = (usage.data?.features ?? []).slice(0, 8).map((f) => ({ label: f.feature, count: f.uses }));
  // Read once per mount: a feature last used over a month ago is one they stopped using.
  const [now] = useState(() => Date.now());
  const unused = (usage.data?.features ?? []).filter((f) => f.last_used_at && now - Date.parse(f.last_used_at) > 30 * 86400000).slice(0, 5);
  return (
    <MemberCard title="What they use" icon={Activity} tone="text-emerald-300" testId="admin-features"
      error={recent.error && usage.error ? recent.error : null}
      empty={!rows.length && !allTime.length && !(recent.error && usage.error) ? 'No use recorded yet.' : null}
      aside="30 days">
      <div className="space-y-3">
        {rows.length > 0 && <Bars rows={rows} />}
        {!rows.length && allTime.length > 0 && <><p className="text-[11px] text-slate-500">All time</p><Bars rows={allTime} /></>}
        {unused.length > 0 && (
          <p className="text-xs text-slate-400" data-testid="admin-features-dropped">
            Not used for a month: {unused.map((f) => f.feature).join(', ')}
          </p>
        )}
      </div>
    </MemberCard>
  );
}

function GapsCard({ section }: { section: UsageSection }) {
  const { error, data } = sectionState(section);
  const open = (data?.capability_gaps ?? []).filter((g) => !g.resolved);
  return (
    <MemberCard title="Asked for, not there yet" icon={Lightbulb} tone="text-amber-300" testId="admin-gaps" error={error}
      empty={!error && !open.length ? 'Nothing asked for that Jarvis cannot do.' : null}>
      <ul className="space-y-1.5">
        {open.slice(0, 8).map((g) => (
          <li key={`${g.capability}-${g.request}`} className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2">
            <div className="flex justify-between gap-2 text-sm"><span className="truncate font-mono text-xs text-amber-100">{g.capability}</span><span className="shrink-0 text-xs text-slate-400">{plural(g.occurrences, 'time')}</span></div>
            {g.request ? <p className="truncate text-[11px] text-slate-400">"{g.request}"</p> : null}
          </li>
        ))}
      </ul>
    </MemberCard>
  );
}

function AssistantCard({ section }: { section: UsageSection }) {
  const { error, data } = sectionState(section);
  const convo = data?.conversations;
  const opens = data?.app_opens_per_day ?? [];
  return (
    <MemberCard title="Talking to Jarvis" icon={Timer} tone="text-purple-300" testId="admin-assistant" error={error}
      empty={!error && !convo?.total && !opens.length ? 'No conversations or app opens in 30 days.' : null} aside="30 days">
      {convo && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <Kv label="Conversations (counted, not read)" value={convo.total} />
            <Kv label="App opens" value={opens.reduce((n, d) => n + d.count, 0)} />
          </div>
          {convo.per_day.length > 1 && (
            <DailyBars testId="admin-assistant-days" title="Conversations by day" height={110}
              data={convo.per_day.map((d) => ({ key: d.day, label: d.day.slice(5), value: d.count }))} format={(v) => plural(v, 'conversation')} />
          )}
          {convo.features.length > 0 && <p className="text-xs text-slate-400">Mostly about: {convo.features.slice(0, 5).map((f) => f.feature).join(', ')}</p>}
        </div>
      )}
    </MemberCard>
  );
}

function ScreenCard({ section, username }: { section: AdminPersonStats['sections']['screen']; username: string }) {
  const { error, data } = sectionState(section);
  const days = data?.days ?? [];
  const avg = days.length ? Math.round(days.reduce((n, d) => n + d.minutes, 0) / days.length) : null;
  return (
    <MemberCard title="Screen time" icon={Smartphone} tone="text-sky-300" testId="admin-screen" error={error}
      empty={!error && !days.length ? 'No screen time reported.' : null}
      aside={<Link to={memberPath(username, 'time')} className="inline-flex min-h-11 items-center gap-1 rounded-lg px-2 hover:bg-white/5">Time controls<ChevronRight size={12} aria-hidden /></Link>}>
      {days.length > 0 && (
        <DailyBars testId="admin-screen-days" title="Screen time, last 14 days" minutes average={avg}
          data={days.map((d) => ({ key: d.day, label: d.day.slice(5), value: d.minutes }))}
          format={(v) => `${Math.floor(v / 60)} h ${v % 60} min`} height={140} />
      )}
    </MemberCard>
  );
}

function GuardianCard({ section, username }: { section: AdminPersonStats['sections']['guardian']; username: string }) {
  const { error, data } = sectionState(section);
  return (
    <MemberCard title="Guardian" icon={ShieldHalf} tone="text-rose-300" testId="admin-guardian" error={error}
      aside={<Link to={memberPath(username, 'activity')} className="inline-flex min-h-11 items-center gap-1 rounded-lg px-2 hover:bg-white/5">Open<ChevronRight size={12} aria-hidden /></Link>}>
      {data && (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <Kv label="Concerns on record" value={data.concerns} />
            <Kv label="Most serious" value={data.levels.high ? `${data.levels.high} high` : data.concerns ? 'none high' : '-'} />
          </div>
          {data.recap_headline ? <p className="text-xs text-slate-300">{data.recap_headline}</p> : null}
        </div>
      )}
    </MemberCard>
  );
}

function LogsCard({ section }: { section: AdminPersonStats['sections']['logs'] }) {
  const { error, data } = sectionState(section);
  const errors = (data?.by_level.ERROR ?? 0) + (data?.by_level.CRITICAL ?? 0);
  return (
    <MemberCard title="Errors logged for them" icon={Bug} tone="text-rose-300" testId="admin-logs" error={error}
      empty={!error && !data?.recent.length ? 'No warnings or errors logged against this person.' : null}
      aside={data ? `${errors} errors, ${data.by_level.WARNING ?? 0} warnings` : undefined}>
      <ul className="space-y-1">
        {(data?.recent ?? []).map((e, i) => (
          <li key={i} className="rounded-lg bg-white/[0.02] px-2 py-1.5 text-xs">
            <span className={e.level === 'WARNING' ? 'text-amber-300' : 'text-rose-300'}>{e.level}</span>
            {e.service ? <span className="text-slate-500"> {e.service}</span> : null}
            <span className="block break-words text-slate-300">{e.message}</span>
          </li>
        ))}
      </ul>
    </MemberCard>
  );
}
