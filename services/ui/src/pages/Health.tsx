import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../context/AuthContext';
import { useHaptics } from '../hooks/useHaptics';
import { api } from '../services/api';
import type { Workout } from '../types/api';
import AchievementsPanel from '../components/wander/AchievementsPanel';
import FamilyActivityCard from '../components/health/FamilyActivityCard';
import RoutePreview from '../components/geo/RoutePreview';
import SensorStatusBanner from '../components/location/SensorStatusBanner';
import HelpTooltip from '../components/ui/HelpTooltip';
import toast from 'react-hot-toast';
import {
  Activity,
  Calendar as CalendarIcon,
  Footprints,
  HeartPulse,
  Loader2,
  Play,
  RefreshCw,
  Sparkles,
  Square,
  TrendingUp,
} from 'lucide-react';
import {
  ACTIVITY_ICONS,
  ACTIVITY_LABELS,
  WORKOUT_OPTIONS,
  formatDuration,
  formatTimeRange,
  type ActivityType,
} from '../lib/workoutMeta';
import {
  activeWorkoutQueryKey,
  pedometerQueryOptions,
  stepsQueryKey,
  workoutsQueryKey,
} from '../lib/healthQueries';
import {
  dailyStepSeries,
  elapsedLabel,
  formatCount,
  paceLabel,
  stepDayLabel,
  stepSourcesLabel,
  stepsSummary,
  syncAdvice,
  syncStatus,
  type DailyStepPoint,
  type StepsSummary,
} from '../lib/healthMetrics';

/**
 * Health shows *recorded* telemetry plus opt-in AI analysis.
 *
 * Two constraints shaped this rewrite:
 *
 * 1. Colours are restricted to the utility set that the light-mode remapping in
 *    `index.css` actually covers (`text-white`, `text-slate-*`, `bg-white/*`,
 *    `border-white/*`, `purple-*`). The previous version used `text-teal-300`,
 *    `text-sky-300`, `bg-orange-500/*` and an emerald-to-teal gradient, none of
 *    which are remapped -- so the page degraded in light mode.
 * 2. `getDailySteps` is shared with the dashboard card under one query key, so
 *    the two screens can no longer disagree about the same number.
 */

/** Pull FastAPI's `detail` out of an axios rejection so a toast says something
 * useful instead of "Request failed with status code 400". */
function serverDetail(err: unknown): string | undefined {
  if (err && typeof err === 'object' && 'response' in err) {
    const detail = (err as { response?: { data?: { detail?: unknown } } }).response?.data
      ?.detail;
    if (typeof detail === 'string') return detail;
  }
  return undefined;
}

const Health = () => {
  const { user } = useAuth();
  const { trigger } = useHaptics();
  const queryClient = useQueryClient();

  const currentUsername = (user?.username || '').toLowerCase() || undefined;
  const [editingGoal, setEditingGoal] = useState(false);
  const [goalDraft, setGoalDraft] = useState('');

  const stepsQuery = useQuery({
    queryKey: stepsQueryKey(currentUsername),
    queryFn: () => api.getDailySteps(currentUsername, 7),
    ...pedometerQueryOptions,
  });
  const workoutsQuery = useQuery({
    queryKey: workoutsQueryKey(currentUsername, 10),
    queryFn: () => api.getWorkouts(currentUsername, 10),
    retry: false,
    staleTime: 30_000,
  });
  const activeQuery = useQuery({
    queryKey: activeWorkoutQueryKey(currentUsername),
    queryFn: () => api.getActiveWorkout(currentUsername),
    retry: false,
    staleTime: 10_000,
  });

  const steps = stepsQuery.data ?? null;
  const workouts = useMemo(() => workoutsQuery.data?.workouts ?? [], [workoutsQuery.data]);
  const activeWorkout = activeQuery.data ?? null;

  const summary = stepsSummary(steps);
  const series = useMemo(() => dailyStepSeries(steps?.daily_steps, 7), [steps?.daily_steps]);
  const seriesMax = useMemo(() => Math.max(1, ...series.map((p) => p.steps)), [series]);
  // A number with no age is a number you cannot trust; geo exposes
  // `last_synced` precisely so a frozen reading is distinguishable from a
  // live one.
  const sync = syncStatus(steps?.last_synced);
  const syncHint = syncAdvice(sync);

  // A background poll must not look like the user pressed Refresh, so this is
  // only true while a *non-first* fetch is in flight.
  const isRefreshing = stepsQuery.isFetching && !stepsQuery.isLoading;
  const refresh = () => {
    trigger('light');
    void Promise.all([
      stepsQuery.refetch(),
      workoutsQuery.refetch(),
      activeQuery.refetch(),
    ]);
  };

  const startMutation = useMutation({
    mutationFn: (activityType: ActivityType) => api.startWorkout(activityType, currentUsername),
    onSuccess: async (res, activityType) => {
      if (res.status === 'already_active') {
        toast.error('You already have a workout in progress. Stop it first.');
        await queryClient.invalidateQueries({ queryKey: activeWorkoutQueryKey(currentUsername) });
        return;
      }
      trigger('success');
      toast.success(
        `${ACTIVITY_LABELS[activityType] || activityType} started — breadcrumbs recording`,
      );
      await queryClient.invalidateQueries({ queryKey: activeWorkoutQueryKey(currentUsername) });
    },
    onError: (err: unknown) => toast.error(serverDetail(err) ?? 'Failed to start workout.'),
  });

  const stopMutation = useMutation({
    mutationFn: () => api.stopWorkout({ user_id: currentUsername }),
    onSuccess: async () => {
      trigger('success');
      toast.success('Workout saved');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: activeWorkoutQueryKey(currentUsername) }),
        queryClient.invalidateQueries({ queryKey: workoutsQueryKey(currentUsername, 10) }),
      ]);
    },
    onError: (err: unknown) => toast.error(serverDetail(err) ?? 'Failed to stop workout.'),
  });

  const goalMutation = useMutation({
    mutationFn: (goal: number) => api.setStepGoal(goal, currentUsername),
    onSuccess: async () => {
      trigger('success');
      setEditingGoal(false);
      toast.success('Step goal updated');
      await queryClient.invalidateQueries({ queryKey: stepsQueryKey(currentUsername) });
    },
    onError: (err: unknown) => toast.error(serverDetail(err) ?? 'Could not save the step goal'),
  });

  const saveGoal = () => {
    const goal = Number(goalDraft);
    if (!Number.isFinite(goal) || goal < 1000 || goal > 100000) {
      toast.error('Goal must be between 1,000 and 100,000');
      return;
    }
    goalMutation.mutate(goal);
  };

  return (
    <div
      className="space-y-4 sm:space-y-5 max-w-6xl mx-auto pb-12 px-1 sm:px-0"
      data-testid="health-page"
    >
      {/* A phone that stopped reporting must never look like a quiet day. */}
      <SensorStatusBanner />

      <header className="glass-panel p-4 sm:p-5 rounded-2xl border border-white/10">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-purple-500/10 border border-purple-500/20 shrink-0">
            <HeartPulse size={24} className="text-purple-400" />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-xl sm:text-2xl font-bold text-white">Health</h1>
            <p className="text-xs text-slate-400 mt-0.5">
              Steps and workouts recorded by your phone. AI analysis runs only when you ask.
            </p>
          </div>
          <button
            onClick={refresh}
            data-testid="health-refresh"
            aria-label="Refresh health data"
            className="glass-button h-9 w-9 shrink-0 px-0 pointer-coarse:h-11 pointer-coarse:w-11"
          >
            <RefreshCw
              size={15}
              className={isRefreshing ? 'animate-spin text-purple-300' : ''}
            />
          </button>
        </div>
        {isRefreshing && (
          <p className="mt-2 text-[11px] text-slate-500" role="status">
            Refreshing…
          </p>
        )}
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 sm:gap-4">
        <StepsCard
          summary={summary}
          series={series}
          seriesMax={seriesMax}
          sources={stepSourcesLabel(steps?.sources)}
          sync={sync}
          syncHint={syncHint}
          loading={stepsQuery.isLoading}
          editingGoal={editingGoal}
          goalDraft={goalDraft}
          savingGoal={goalMutation.isPending}
          onOpenGoal={() => {
            trigger('light');
            setGoalDraft(String(summary.goal));
            setEditingGoal(true);
          }}
          onGoalDraft={setGoalDraft}
          onCancelGoal={() => setEditingGoal(false)}
          onSaveGoal={saveGoal}
        />

        <AchievementsPanel userId={currentUsername} />
        <FamilyActivityCard />
      </div>

      <TrendsPanel currentUsername={currentUsername} />

      <section className="space-y-3" aria-labelledby="workouts-heading">
        <div className="flex items-center justify-between px-1 gap-3">
          <h2
            id="workouts-heading"
            className="text-sm font-semibold text-slate-200 flex items-center gap-2 shrink-0"
          >
            <Activity size={16} className="text-purple-400" />
            Workouts
          </h2>
          <span className="text-xs text-slate-500 text-right">
            Distance and speed come from GPS
          </span>
        </div>

        <div className="glass-panel p-4 rounded-2xl border border-white/5">
          {activeQuery.isLoading ? (
            <div className="h-11 skeleton rounded-xl" aria-hidden />
          ) : activeWorkout ? (
            <ActiveWorkoutRow
              workout={activeWorkout}
              stopping={stopMutation.isPending}
              onStop={() => {
                trigger('medium');
                stopMutation.mutate();
              }}
            />
          ) : (
            <>
              <p className="text-[11px] text-slate-400 mb-2.5 flex items-center gap-1.5">
                <Play size={11} className="text-purple-400" />
                Start tracking an activity
              </p>
              <div className="flex flex-wrap gap-2">
                {WORKOUT_OPTIONS.map((opt) => {
                  const Icon = opt.icon;
                  return (
                    <button
                      key={opt.type}
                      onClick={() => {
                        trigger('medium');
                        startMutation.mutate(opt.type);
                      }}
                      disabled={startMutation.isPending}
                      className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] border border-white/10 text-xs font-semibold text-slate-200 transition-all min-h-11 pointer-coarse:min-h-11 disabled:opacity-50"
                    >
                      <Icon size={14} />
                      {opt.label}
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>

        {workouts.length > 0 ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 sm:gap-4">
            {workouts.map((workout) => (
              <WorkoutCard key={workout.id} workout={workout} />
            ))}
          </div>
        ) : (
          !activeWorkout &&
          !workoutsQuery.isLoading && (
            <div className="glass-panel p-6 rounded-2xl border border-white/5 text-center">
              <p className="text-sm text-slate-400">No workouts recorded yet.</p>
              <p className="text-xs text-slate-500 mt-1">
                Start one above — walks and runs also estimate steps from GPS when your
                phone&apos;s pedometer isn&apos;t available.
              </p>
            </div>
          )
        )}
      </section>
    </div>
  );
};

interface StepsCardProps {
  summary: StepsSummary;
  series: DailyStepPoint[];
  seriesMax: number;
  sources: string | null;
  sync: ReturnType<typeof syncStatus>;
  syncHint: string | null;
  loading: boolean;
  editingGoal: boolean;
  goalDraft: string;
  savingGoal: boolean;
  onOpenGoal: () => void;
  onGoalDraft: (value: string) => void;
  onCancelGoal: () => void;
  onSaveGoal: () => void;
}

function StepsCard({
  summary,
  series,
  seriesMax,
  sources,
  sync,
  syncHint,
  loading,
  editingGoal,
  goalDraft,
  savingGoal,
  onOpenGoal,
  onGoalDraft,
  onCancelGoal,
  onSaveGoal,
}: StepsCardProps) {
  const radius = 42;
  const circumference = 2 * Math.PI * radius;

  return (
    <section
      className="glass-panel p-4 rounded-2xl border border-white/5 flex flex-col gap-3"
      aria-labelledby="steps-heading"
      data-testid="steps-card"
    >
      <div className="flex items-center gap-4">
        <div className="relative w-24 h-24 shrink-0">
          <svg viewBox="0 0 100 100" className="w-24 h-24 -rotate-90" aria-hidden>
            <circle
              cx="50"
              cy="50"
              r={radius}
              fill="none"
              stroke="rgba(255,255,255,0.08)"
              strokeWidth="10"
            />
            <circle
              cx="50"
              cy="50"
              r={radius}
              fill="none"
              stroke="currentColor"
              strokeWidth="10"
              strokeLinecap="round"
              className="text-purple-400 transition-all duration-700"
              strokeDasharray={`${(summary.percent / 100) * circumference} ${circumference}`}
            />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <Footprints size={16} className="text-purple-400 mb-0.5" />
            <span className="text-sm font-bold text-white">{summary.percent}%</span>
          </div>
        </div>

        <div className="min-w-0 flex-1">
          <h3
            id="steps-heading"
            className="text-[11px] text-slate-400 uppercase tracking-wider font-semibold"
          >
            Steps Today
          </h3>
          {loading ? (
            <div className="mt-2 space-y-2" aria-hidden>
              <div className="h-7 w-24 skeleton rounded" />
              <div className="h-3 w-32 skeleton rounded" />
            </div>
          ) : summary.hasData ? (
            <>
              <p className="text-3xl font-bold text-white mt-0.5 leading-none">
                {formatCount(summary.today)}
              </p>
              <p className="text-[11px] text-slate-500 mt-1">
                of {formatCount(summary.goal)} goal
                {summary.reachedGoal ? ' — reached' : ''}
              </p>
              <button
                type="button"
                onClick={onOpenGoal}
                className="mt-0.5 text-[11px] text-purple-300 hover:text-purple-200 underline underline-offset-2 min-h-11 pointer-coarse:min-h-11"
              >
                Edit goal
              </button>
            </>
          ) : (
            <p className="text-xs text-slate-400 mt-1.5">
              No pedometer reading yet. Open the app on your phone to sync.
            </p>
          )}
        </div>
      </div>

      {editingGoal && (
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="number"
            min={1000}
            max={100000}
            step={500}
            value={goalDraft}
            onChange={(e) => onGoalDraft(e.target.value)}
            aria-label="Daily step goal"
            className="glass-input px-3 py-2 text-sm w-32 h-9 pointer-coarse:h-11"
          />
          <button
            type="button"
            onClick={onSaveGoal}
            disabled={savingGoal}
            className="glass-button px-4 text-xs min-h-11 pointer-coarse:min-h-11"
          >
            {savingGoal ? <Loader2 size={13} className="animate-spin" /> : 'Save'}
          </button>
          <button
            type="button"
            onClick={onCancelGoal}
            className="glass-button px-4 text-xs min-h-11 pointer-coarse:min-h-11"
          >
            Cancel
          </button>
        </div>
      )}

      {/* Sorted oldest-first; the previous version trusted Redis hash field
          order, so a bucket could be labelled with the wrong weekday. */}
      {summary.hasData && series.length > 0 && (
        <div className="flex items-end gap-1.5 h-14" data-testid="steps-sparkline">
          {series.map((point) => (
            <div key={point.date} className="flex-1 flex flex-col items-center gap-1 min-w-0">
              <div
                className="w-full rounded-t bg-purple-500/50"
                style={{ height: `${Math.max(6, (point.steps / seriesMax) * 100)}%` }}
                title={`${stepDayLabel(point.date)}: ${formatCount(point.steps)} steps`}
              />
              <span className="text-[9px] text-slate-500 truncate w-full text-center">
                {point.weekday}
              </span>
            </div>
          ))}
        </div>
      )}

      {(sources || sync.label || syncHint) && (
        <div className="text-[11px] text-slate-500 space-y-0.5">
          {sources && <p data-testid="steps-sources">Reported by {sources}</p>}
          {(sync.label || syncHint) && (
            <p
              data-testid="steps-sync-status"
              data-freshness={sync.freshness}
              className={sync.stale ? 'text-slate-400' : undefined}
            >
              {syncHint ?? `Updated ${sync.label}`}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function ActiveWorkoutRow({
  workout,
  stopping,
  onStop,
}: {
  workout: Pick<Workout, 'activity_type' | 'start_time'>;
  stopping: boolean;
  onStop: () => void;
}) {
  const Icon = ACTIVITY_ICONS[workout.activity_type] || Footprints;
  return (
    <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
      <div className="flex items-center gap-3 min-w-0">
        <span className="relative flex h-3 w-3 shrink-0">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-purple-400 opacity-75" />
          <span className="relative inline-flex rounded-full h-3 w-3 bg-purple-500" />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-white flex items-center gap-2">
            <Icon size={16} className="text-purple-400 shrink-0" />
            <span className="truncate">
              {ACTIVITY_LABELS[workout.activity_type] || workout.activity_type} in progress
            </span>
          </p>
          <p className="text-[11px] text-slate-400">
            Started{' '}
            {new Date(workout.start_time * 1000).toLocaleTimeString([], {
              hour: 'numeric',
              minute: '2-digit',
            })}
            {' · elapsed '}
            <ElapsedTimer startTime={workout.start_time} />
          </p>
        </div>
      </div>
      <button
        onClick={onStop}
        disabled={stopping}
        className="flex items-center gap-2 px-4 py-2 rounded-xl bg-purple-600/80 hover:bg-purple-500 text-white text-xs font-bold transition-all min-h-11 pointer-coarse:min-h-11 disabled:opacity-50 shrink-0"
      >
        <Square size={13} />
        {stopping ? 'Saving…' : 'Stop & Save'}
      </button>
    </div>
  );
}

/** Ticks once a second while a workout runs so elapsed time reads as live. */
function ElapsedTimer({ startTime }: { startTime: number }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(id);
  }, []);
  return <span className="font-mono">{elapsedLabel(startTime, now)}</span>;
}

function TrendsPanel({ currentUsername }: { currentUsername?: string }) {
  const { trigger } = useHaptics();
  const queryClient = useQueryClient();
  const [analysis, setAnalysis] = useState<string | null>(null);

  // `analyzeActivityTrends` is a POST and generates content, so it must stay
  // opt-in -- it is never called on mount, only by the button.
  const trendsQueryKey = ['activity-trends', currentUsername ?? 'me'] as const;
  const trendsQuery = useQuery({
    queryKey: trendsQueryKey,
    queryFn: () => api.getActivityTrends(currentUsername, 7, false),
    retry: false,
    staleTime: 5 * 60_000,
  });
  const analyzeMutation = useMutation({
    mutationFn: () => api.analyzeActivityTrends(currentUsername, 7, true),
    onSuccess: (res) => {
      trigger('success');
      // The analysis response carries the same stats payload as the plain read,
      // so seed the cache rather than making the user wait on a second request.
      queryClient.setQueryData(trendsQueryKey, res);
      setAnalysis(res.analysis_available ? res.analysis : null);
    },
    onError: (err: unknown) => toast.error(serverDetail(err) ?? 'Analysis failed'),
  });

  const trends = trendsQuery.data ?? null;
  const insights = analysis ?? (trends?.analysis_available ? trends.analysis : null);
  const loading = analyzeMutation.isPending;
  // Keyed on whether an *analysis* has been produced, not on whether the stats
  // arrived -- otherwise a failed stats fetch makes a completed analysis look
  // like it never ran.
  const hasAnalyzed = Boolean(insights);

  return (
    <section className="glass-panel p-4 rounded-2xl border border-white/5" aria-labelledby="trends-heading">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div className="flex items-center gap-1.5 text-[11px] text-slate-400 uppercase tracking-wider font-semibold">
          <TrendingUp size={13} className="text-purple-400" />
          <h2 id="trends-heading">7-Day Activity Trends</h2>
          <HelpTooltip docName="HEALTH_STEPS.md" sectionTitle="Activity trends" />
        </div>
        <button
          onClick={() => {
            trigger('light');
            analyzeMutation.mutate();
          }}
          disabled={loading}
          data-testid="analyze-activity"
          className="glass-button flex items-center gap-1.5 px-3 text-[11px] font-semibold min-h-11 pointer-coarse:min-h-11"
          title="AI analysis runs only when you request it"
        >
          {loading ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Sparkles size={12} />
          )}
          {hasAnalyzed ? 'Re-analyze' : 'Analyze activity'}
        </button>
      </div>

      {loading && !trends && !hasAnalyzed ? (
        <div className="space-y-3" aria-hidden>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-16 skeleton rounded-xl" />
            ))}
          </div>
          <div className="h-10 skeleton rounded-xl" />
        </div>
      ) : trends || hasAnalyzed ? (
        <div className="space-y-3">
          {trends && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <Stat
                label="Steps Avg/Day"
                value={
                  (trends.steps_avg ?? trends.steps_average) != null
                    ? formatCount(trends.steps_avg ?? trends.steps_average ?? 0)
                    : '—'
                }
              />
              <Stat label="Workouts" value={formatCount(trends.workout_count)} />
              <Stat
                label="Workout Miles"
                value={(trends.workout_distance_miles || 0).toFixed(1)}
              />
              <Stat label="Drive Cost" value={`$${(trends.drive_cost_usd || 0).toFixed(2)}`} />
            </div>
          )}

          {insights ? (
            <div className="p-3 rounded-xl bg-purple-500/10 border border-purple-500/20">
              <div className="flex items-center gap-1.5 text-[11px] font-semibold text-purple-300 uppercase tracking-wider mb-1">
                <Sparkles size={11} />
                AI Insight
              </div>
              <p className="text-xs text-slate-300 leading-relaxed whitespace-pre-wrap">
                {insights}
              </p>
            </div>
          ) : (
            <div className="p-3 rounded-xl bg-white/[0.02] border border-white/5 text-[11px] text-slate-500">
              {loading
                ? 'Analyzing your activity with AI…'
                : 'AI analysis unavailable right now — the stats above are live from your recorded data.'}
            </div>
          )}
        </div>
      ) : (
        <div className="py-6 text-center space-y-2" data-testid="analysis-opt-in">
          <p className="text-sm text-slate-500">
            Fitness and health analysis only runs when you ask for it.
          </p>
          <p className="text-xs text-slate-600">
            The steps and workouts below are recorded data — no analysis is generated until
            you request it.
          </p>
          <button
            type="button"
            onClick={() => {
              trigger('light');
              analyzeMutation.mutate();
            }}
            disabled={loading}
            className="glass-button px-5 py-2 text-xs font-semibold min-h-11 pointer-coarse:min-h-11"
          >
            Analyze my activity
          </button>
        </div>
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="p-2.5 rounded-xl bg-white/[0.03] border border-white/5">
      <span className="text-[10px] text-slate-400 uppercase font-semibold">{label}</span>
      <p className="text-base font-bold text-purple-300 mt-0.5">{value}</p>
    </div>
  );
}

function WorkoutCard({ workout }: { workout: Workout }) {
  const Icon = ACTIVITY_ICONS[workout.activity_type] || Footprints;
  const pace = paceLabel(workout.distance_miles, workout.duration_seconds);
  return (
    <article className="glass-card p-4 rounded-2xl">
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="p-2 rounded-lg bg-purple-500/10 border border-purple-500/20 shrink-0">
            <Icon size={16} className="text-purple-400" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-white truncate">
              {ACTIVITY_LABELS[workout.activity_type] || workout.activity_type}
            </p>
            <p className="text-[11px] text-slate-500 flex items-center gap-1">
              <CalendarIcon size={10} />
              {formatTimeRange(workout.start_time, workout.end_time || workout.start_time)}
            </p>
          </div>
        </div>
        {workout.steps != null && (
          <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-purple-500/10 border border-purple-500/20 text-[11px] font-semibold text-purple-300 shrink-0">
            <Footprints size={11} />
            {formatCount(workout.steps)}
            {workout.steps_source === 'gps_estimate' && (
              <span className="text-[9px] text-slate-500 font-normal">est</span>
            )}
          </div>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="p-2 rounded-lg bg-white/[0.02]">
          <span className="text-[9px] text-slate-500 uppercase">Distance</span>
          <p className="text-xs font-bold text-white">
            {(workout.distance_miles || 0).toFixed(2)} mi
          </p>
        </div>
        <div className="p-2 rounded-lg bg-white/[0.02]">
          <span className="text-[9px] text-slate-500 uppercase">Duration</span>
          <p className="text-xs font-bold text-white">
            {formatDuration(workout.duration_seconds || 0)}
          </p>
        </div>
        <div className="p-2 rounded-lg bg-white/[0.02]">
          <span className="text-[9px] text-slate-500 uppercase">Pace</span>
          <p className="text-xs font-bold text-white">{pace ? `${pace} /mi` : '—'}</p>
        </div>
      </div>

      {workout.notes && (
        <p className="text-[11px] text-slate-400 mt-2 italic">{workout.notes}</p>
      )}

      <RoutePreview
        id={workout.id}
        completed={workout.status === 'completed'}
        fetcher={() => api.getWorkoutRoute(workout.id).then((r) => r.points)}
      />
    </article>
  );
}

export default Health;