import { useQuery } from '@tanstack/react-query';
import { AppWindow, BarChart3, Gauge, MapPinned, Moon } from 'lucide-react';
import { api } from '../../../services/api';
import MemberCard from '../MemberCard';
import ConcernsCard from '../ConcernsCard';
import RequestsCard from '../../guardian/RequestsCard';
import LiveFamilyMap from '../../geo/LiveFamilyMap';
import { DailyBars, TimeRing } from '../charts';
import { averageMinutes, dayBars, nextRoutineLine, statusQuery, usageQuery } from '../../../lib/memberPage';
import { appName, duration } from '../../../lib/phoneUsageFormat';
import { formatMinutes, localDay } from '../../../lib/healthHub';
import { lockedLine } from '../../../lib/screenTimeText';
import { geoZonesQueryKey, zonePlaces } from '../../../lib/wanderQueries';
import { useFamilyDirectory } from '../../chat/familyDirectory';

const minutes = (m: number) => duration(m * 60000);

/** Today against the limit, and the next routine. */
export function TodayCard({ username }: { username: string }) {
  const { data: status, isLoading, isError } = useQuery(statusQuery(username));
  const next = nextRoutineLine(status);
  return (
    <MemberCard title="Today" icon={Gauge} testId="member-today" isLoading={isLoading} error={isError ? 'Screen time could not be loaded.' : null}>
      {status && (
        <div className="flex items-center gap-4">
          <TimeRing used={status.used_min} limit={status.limit_min} />
          <ul className="min-w-0 space-y-1 text-xs text-slate-300">
            <li data-testid="member-today-left">
              {status.locked ? lockedLine(status) : status.remaining_min !== null ? `${minutes(status.remaining_min)} left today` : 'No daily limit today.'}
            </li>
            {status.bonus_min > 0 && <li>Bonus today: {minutes(status.bonus_min)}</li>}
            {next && <li data-testid="member-next-routine">{next}</li>}
            {(status.blocked_apps?.length ?? 0) > 0 && <li>{status.blocked_apps?.length} app{status.blocked_apps?.length === 1 ? '' : 's'} closed now</li>}
          </ul>
        </div>
      )}
    </MemberCard>
  );
}

/** The last seven days of screen time against the limit, with the average. */
export function WeekCard({ username }: { username: string }) {
  const { data: usage, isLoading, isError } = useQuery(usageQuery(username));
  const { data: status } = useQuery(statusQuery(username));
  const bars = dayBars(usage?.days || [], status?.rules, 7);
  const avg = averageMinutes(bars);
  const limit = status?.rules.daily_limit_min ?? status?.limit_min ?? null;
  return (
    <MemberCard
      title="Screen time, 7 days"
      icon={BarChart3}
      testId="member-week"
      isLoading={isLoading}
      error={isError ? 'Screen time could not be loaded.' : null}
      empty={avg === null ? 'No screen time reported in the last 7 days.' : null}
    >
      <DailyBars
        testId="member-week-chart"
        title="Screen time per day for the last 7 days"
        data={bars.map((b) => ({ key: b.day, label: b.label, value: b.minutes, over: b.over }))}
        format={minutes}
        tickFormat={formatMinutes}
        limit={limit}
        average={avg}
      />
    </MemberCard>
  );
}

/** The day's top apps by time, as bars. */
export function TopAppsCard({ username }: { username: string }) {
  const { data: usage, isLoading, isError } = useQuery(usageQuery(username));
  const today = usage?.days.find((d) => d.day === localDay());
  const apps = [...(today?.apps || [])].sort((a, b) => b.millis - a.millis).slice(0, 6);
  const top = Math.max(1, ...apps.map((a) => a.millis));
  return (
    <MemberCard
      title="Top apps today"
      icon={AppWindow}
      testId="member-top-apps"
      isLoading={isLoading}
      error={isError ? 'Apps could not be loaded.' : null}
      empty={apps.length === 0 ? 'No app use reported today.' : null}
    >
      <ul className="space-y-2">
        {apps.map((a) => (
          <li key={a.package} className="space-y-1 text-xs" title={`${appName(a.package)}: ${duration(a.millis)}`}>
            <div className="flex justify-between gap-2">
              <span className="truncate text-slate-200">{appName(a.package)}</span>
              <span className="shrink-0 text-slate-400">{duration(a.millis)}</span>
            </div>
            <div className="h-1.5 rounded-full bg-white/5">
              <div className="h-1.5 rounded-full bg-sky-400" style={{ width: `${Math.max(2, (a.millis / top) * 100)}%` }} />
            </div>
          </li>
        ))}
      </ul>
    </MemberCard>
  );
}

/** Where they are, on the family map (their pin centred). */
export function MiniMapCard({ username, height = 200 }: { username: string; height?: number }) {
  const { data: zones } = useQuery({ queryKey: geoZonesQueryKey(), queryFn: () => api.getGeoZones(), retry: false, staleTime: 5 * 60_000 });
  const people = useFamilyDirectory();
  return (
    <MemberCard title="Location" icon={MapPinned} tone="text-emerald-300" testId="member-minimap">
      <LiveFamilyMap height={height} focusUserId={username} zones={zonePlaces(zones)} people={people} />
    </MemberCard>
  );
}

/** Last night's sleep, when they share it under the health sharing rules. */
export function LastNightCard({ username, name }: { username: string; name: string }) {
  const day = localDay();
  const { data, isLoading, isError } = useQuery({ queryKey: ['health-family', day], queryFn: () => api.getHealthFamily(day), staleTime: 120_000, retry: false });
  const sleep = data?.people.find((p) => p.user_id === username)?.sleep;
  return (
    <MemberCard
      title="Last night"
      icon={Moon}
      tone="text-indigo-300"
      testId="member-last-night"
      isLoading={isLoading}
      error={isError ? 'Sleep could not be loaded.' : null}
      empty={!sleep ? `${name} does not share sleep with you.` : !sleep.logged ? 'No sleep recorded for last night.' : null}
    >
      {sleep && (
        <p className="text-sm text-slate-200">
          <span className="text-lg font-semibold text-white">{formatMinutes(sleep.duration_minutes)}</span>
          {sleep.label ? ` · ${sleep.label}` : ''}
          {sleep.score !== null ? ` · score ${sleep.score}` : ''}
        </p>
      )}
    </MemberCard>
  );
}

/** Aura's overview: today, the week, what they want, what to watch for, where they are. */
export default function OverviewSection({ username, name }: { username: string; name: string }) {
  return (
    <>
      <TodayCard username={username} />
      <RequestsCard username={username} name={name} />
      <WeekCard username={username} />
      <ConcernsCard username={username} name={name} />
      <TopAppsCard username={username} />
      <MiniMapCard username={username} />
      <LastNightCard username={username} name={name} />
    </>
  );
}
