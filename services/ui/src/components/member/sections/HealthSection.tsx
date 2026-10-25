import { useQueries, useQuery } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { Footprints, Moon, Smartphone } from 'lucide-react';
import { api } from '../../../services/api';
import MemberCard from '../MemberCard';
import { DailyBars } from '../charts';
import UsageTiles from '../../health/UsageTiles';
import { usageQuery } from '../../../lib/memberPage';
import { formatMinutes, localDay, shiftDay } from '../../../lib/healthHub';
import { stepsQueryKey } from '../../../lib/healthQueries';

const NIGHTS = 14;
const short = (day: string) => new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { day: 'numeric' });

/** Sleep for the last 14 nights, as shared with the viewer (the health sharing rules decide). */
function SleepCard({ username, name }: { username: string; name: string }) {
  const days = Array.from({ length: NIGHTS }, (_, i) => shiftDay(localDay(), i - NIGHTS + 1));
  const nights = useQueries({
    queries: days.map((day) => ({ queryKey: ['health-family', day], queryFn: () => api.getHealthFamily(day), staleTime: 120_000, retry: false })),
  });
  const loading = nights.some((q) => q.isLoading);
  const failed = nights.every((q) => q.isError);
  const sleep = nights.map((q) => q.data?.people.find((p) => p.user_id === username)?.sleep);
  const shared = sleep.some(Boolean);
  const points = days.map((day, i) => ({
    key: day,
    label: short(day),
    value: sleep[i]?.logged && sleep[i]?.duration_minutes !== null ? (sleep[i]?.duration_minutes ?? null) : null,
  }));
  const logged = points.filter((p) => p.value !== null);
  const avg = logged.length ? Math.round(logged.reduce((n, p) => n + (p.value || 0), 0) / logged.length) : null;
  return (
    <MemberCard
      title="Sleep, 14 nights"
      icon={Moon}
      tone="text-indigo-300"
      testId="member-sleep"
      wide
      isLoading={loading}
      error={failed ? 'Sleep could not be loaded.' : null}
      empty={!shared ? `${name} does not share sleep with you.` : logged.length === 0 ? 'No sleep recorded in the last 14 nights.' : null}
    >
      <DailyBars testId="member-sleep-chart" title="Hours of sleep per night, last 14 nights" data={points} format={formatMinutes} average={avg} />
    </MemberCard>
  );
}

/** Steps for the last 14 days, from their phone's pedometer (their activity sharing applies). */
function StepsCard({ username, name }: { username: string; name: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: [...stepsQueryKey(username), NIGHTS],
    queryFn: () => api.getDailySteps(username, NIGHTS),
    retry: false,
    staleTime: 60_000,
  });
  const days = Array.from({ length: NIGHTS }, (_, i) => shiftDay(localDay(), i - NIGHTS + 1));
  const points = days.map((day) => ({ key: day, label: short(day), value: data?.daily_steps?.[day] ?? null }));
  const counted = points.filter((p) => p.value !== null);
  const avg = counted.length ? Math.round(counted.reduce((n, p) => n + (p.value || 0), 0) / counted.length) : null;
  const refused = isAxiosError(error) && (error.response?.status === 403 || error.response?.status === 404);
  return (
    <MemberCard
      title="Steps, 14 days"
      icon={Footprints}
      tone="text-emerald-300"
      testId="member-steps"
      wide
      isLoading={isLoading}
      error={refused ? `${name}'s steps are not shared with you.` : error ? 'Steps could not be loaded.' : null}
      empty={counted.length === 0 ? 'No steps reported in the last 14 days.' : null}
    >
      <DailyBars
        testId="member-steps-chart"
        title="Steps per day, last 14 days"
        data={points}
        format={(v) => v.toLocaleString()}
        limit={data?.goal ?? null}
        limitLabel="goal"
        average={avg}
      />
    </MemberCard>
  );
}

/** Today's phone habits: when use began and ended, how much, how often. */
function HabitsCard({ username }: { username: string }) {
  const { data, isLoading, isError } = useQuery(usageQuery(username));
  const today = data?.days.find((d) => d.day === localDay());
  return (
    <MemberCard
      title="Phone habits today"
      icon={Smartphone}
      testId="member-habits"
      wide
      isLoading={isLoading}
      error={isError ? 'Phone use could not be loaded.' : null}
      empty={!today ? 'No phone use reported today.' : null}
    >
      {today && <UsageTiles day={today} />}
    </MemberCard>
  );
}

export default function HealthSection({ username, name }: { username: string; name: string }) {
  return (
    <>
      <SleepCard username={username} name={name} />
      <StepsCard username={username} name={name} />
      <HabitsCard username={username} />
    </>
  );
}
