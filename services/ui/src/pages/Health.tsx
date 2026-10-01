import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '../context/AuthContext';
import { useHaptics } from '../hooks/useHaptics';
import { api } from '../services/api';
import type { Workout, RoutePoint, StepsResponse, ActivityTrendsResponse } from '../types/api';
import AchievementsPanel from '../components/wander/AchievementsPanel';
import FamilyActivityCard from '../components/health/FamilyActivityCard';
import RoutePreview from '../components/geo/RoutePreview';
import toast from 'react-hot-toast';
import {
  Activity,
  Calendar as CalendarIcon,
  Flame,
  Footprints,
  HeartPulse,
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

const Health = () => {
  const { user } = useAuth();
  const { trigger } = useHaptics();

  const currentUsername = (user?.username || '').toLowerCase();

  // Steps (hardware pedometer) & trends
  const [steps, setSteps] = useState<StepsResponse | null>(null);
  const [trends, setTrends] = useState<ActivityTrendsResponse | null>(null);
  const [trendsLoading, setTrendsLoading] = useState(false);
  const [trendsAnalysis, setTrendsAnalysis] = useState<string | null>(null);
  const [editingGoal, setEditingGoal] = useState(false);
  const [goalDraft, setGoalDraft] = useState('10000');
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Workouts
  const [workouts, setWorkouts] = useState<Workout[]>([]);
  const [activeWorkout, setActiveWorkout] = useState<Workout | null>(null);

  // Route previews (fetched lazily per card)
  const [routePoints, setRoutePoints] = useState<Record<string, RoutePoint[]>>({});
  const routeCache = useRef<Record<string, RoutePoint[]>>({});

  const fetchStepsAndWorkouts = useCallback(async () => {
    try {
      const stepsRes = await api.getDailySteps(currentUsername || undefined, 7);
      setSteps(stepsRes);
    } catch {
      // Steps require pedometer hardware; absence is not an error state
      setSteps(null);
    }
    try {
      // Scoped to this user. Passing nothing returned `geo:workouts:all`, which
      // is every user's workouts -- including unlabelled ones, since this
      // page never rendered an owner.
      const workoutsRes = await api.getWorkouts(currentUsername || undefined, 10);
      setWorkouts(workoutsRes.workouts || []);
    } catch {
      setWorkouts([]);
    }
    try {
      // Asked separately because an in-progress session is deliberately not in
      // the history index -- geo only files it there once it stops. The old
      // `status === 'active'` scan could therefore never match, so a running
      // workout was unrecoverable after a reload and Stop went stale.
      setActiveWorkout(await api.getActiveWorkout(currentUsername || undefined));
    } catch {
      setActiveWorkout(null);
    }
  }, [currentUsername]);

  const fetchTrends = useCallback(
    async (refresh = false) => {
      setTrendsLoading(true);
      try {
        const res = await api.analyzeActivityTrends(currentUsername || undefined, 7, refresh);
        setTrends(res);
        setTrendsAnalysis(res.analysis_available ? res.analysis : null);
      } catch {
        setTrends(null);
        setTrendsAnalysis(null);
      } finally {
        setTrendsLoading(false);
      }
    },
    [currentUsername]
  );

  useEffect(() => {
    let active = true;
    const init = async () => {
      try {
        await fetchStepsAndWorkouts();
      } catch (err) {
        if (active) {
          console.error('Initial health load failed:', err);
        }
      }
    };
    void init();
    return () => {
      active = false;
    };
  }, [fetchStepsAndWorkouts]);

  // Live polling: step counts and workout state appear as events happen.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible') return;
      void fetchStepsAndWorkouts();
    }, 15000);
    return () => window.clearInterval(timer);
  }, [fetchStepsAndWorkouts]);

  // Also refresh the moment the user comes back to the tab
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        void fetchStepsAndWorkouts();
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [fetchStepsAndWorkouts]);

  // Active workout timer
  const [nowSeconds, setNowSeconds] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = setInterval(() => setNowSeconds(Date.now() / 1000), 30000);
    return () => clearInterval(timer);
  }, []);

  const handleStartWorkout = async (activityType: ActivityType) => {
    trigger('medium');
    try {
      const res = await api.startWorkout(activityType, currentUsername || undefined);
      if (res.status === 'already_active') {
        toast.error('You already have a workout in progress. Stop it first.');
        // Ask geo for the running session directly; it is not in the history
        // list, which is why the old list scan never found it.
        setActiveWorkout(await api.getActiveWorkout(currentUsername || undefined));
        return;
      }
      setActiveWorkout(res.workout);
      toast.success(`${ACTIVITY_LABELS[activityType] || activityType} started — breadcrumbs recording`);
    } catch (err: unknown) {
      const errorMsg =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
          : undefined;
      toast.error(errorMsg || 'Failed to start workout.');
    }
  };

  const handleStopWorkout = async () => {
    if (!activeWorkout) return;
    trigger('medium');
    try {
      const res = await api.stopWorkout({ user_id: currentUsername || undefined });
      toast.success('Workout saved');
      setActiveWorkout(null);
      // Prepend the finished workout
      setWorkouts((prev) => [res.workout, ...prev].slice(0, 10));
      await fetchStepsAndWorkouts();
    } catch (err: unknown) {
      const errorMsg =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
          : undefined;
      toast.error(errorMsg || 'Failed to stop workout.');
    }
  };

  // Lazy route loading for workout cards
  const loadRoute = useCallback(async (key: string, loader: () => Promise<RoutePoint[]>) => {
    if (routeCache.current[key]) {
      setRoutePoints((prev) => ({ ...prev, [key]: routeCache.current[key] }));
      return;
    }
    try {
      const points = await loader();
      routeCache.current[key] = points;
      setRoutePoints((prev) => ({ ...prev, [key]: points }));
    } catch {
      // Route unavailable (e.g. no breadcrumbs) — card renders without map
    }
  }, []);

  // Save the daily step goal from the ring card
  const saveGoal = async () => {
    const goal = Number(goalDraft);
    if (!Number.isFinite(goal) || goal < 1000 || goal > 100000) {
      toast.error('Goal must be between 1,000 and 100,000');
      return;
    }
    try {
      await api.setStepGoal(goal, currentUsername || undefined);
      setEditingGoal(false);
      await fetchStepsAndWorkouts();
    } catch {
      toast.error('Could not save the step goal');
    }
  };

  // Steps ring progress
  const stepsGoal = steps?.goal || 10000;
  const stepsToday = steps?.today || 0;
  const stepsPct = Math.min(100, Math.round((stepsToday / stepsGoal) * 100));

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12" data-testid="health-page">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 glass-panel p-6 rounded-2xl border border-white/10 shadow-xl">
        <div className="space-y-1">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
              <HeartPulse size={26} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-2xl sm:text-3xl font-bold bg-gradient-to-r from-emerald-300 via-teal-300 to-indigo-300 bg-clip-text text-transparent">
                  Health
                </h1>
                <span className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                  Body
                </span>
              </div>
              <p className="text-xs sm:text-sm text-slate-400">
                Steps, workouts, and achievements from your recorded data. AI analysis runs only when you ask.
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              void (async () => {
                setIsRefreshing(true);
                try {
                  await fetchStepsAndWorkouts();
                } finally {
                  setIsRefreshing(false);
                }
              })();
            }}
            disabled={isRefreshing}
            className="glass-button flex items-center gap-2 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white rounded-xl"
            title="Refresh Health Data"
          >
            <RefreshCw size={14} className={isRefreshing ? 'animate-spin text-emerald-400' : ''} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Steps Ring + Achievements + Trends */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Daily Steps Ring (hardware pedometer) */}
        <div className="glass-panel p-5 rounded-2xl border border-white/5 flex items-center gap-5">
          <div className="relative w-24 h-24 shrink-0">
            <svg viewBox="0 0 100 100" className="w-24 h-24 -rotate-90">
              <circle cx="50" cy="50" r="42" fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="10" />
              <circle
                cx="50"
                cy="50"
                r="42"
                fill="none"
                stroke={stepsPct >= 100 ? '#10b981' : '#a78bfa'}
                strokeWidth="10"
                strokeLinecap="round"
                strokeDasharray={`${(stepsPct / 100) * 2 * Math.PI * 42} ${2 * Math.PI * 42}`}
                className="transition-all duration-700"
              />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <Footprints size={18} className="text-purple-400 mb-0.5" />
              <span className="text-sm font-bold text-white">{stepsPct}%</span>
            </div>
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-[11px] text-slate-400 uppercase tracking-wider font-semibold">
              <Flame size={12} className="text-orange-400" />
              Steps Today
            </div>
            {steps && Object.keys(steps.daily_steps || {}).length > 0 ? (
              <>
                <p className="text-2xl font-bold text-white mt-0.5">{stepsToday.toLocaleString()}</p>
                <p className="text-[11px] text-slate-500">
                  of {stepsGoal.toLocaleString()} goal
                  {stepsPct >= 100 ? ' — goal reached! 🎉' : ''}
                  <button
                    type="button"
                    className="ml-1.5 underline hover:text-purple-300"
                    onClick={() => {
                      setGoalDraft(String(stepsGoal));
                      setEditingGoal(true);
                    }}
                  >
                    edit goal
                  </button>
                </p>
                {editingGoal && (
                  <div className="flex items-center gap-1 mt-1">
                    <input
                      type="number"
                      min={1000}
                      max={100000}
                      step={500}
                      value={goalDraft}
                      onChange={(e) => setGoalDraft(e.target.value)}
                      aria-label="Daily step goal"
                      className="glass-input px-2 py-1 text-xs w-24"
                    />
                    <button type="button" className="glass-button px-2 py-1 text-[11px]" onClick={() => void saveGoal()}>
                      Save
                    </button>
                    <button
                      type="button"
                      className="glass-button px-2 py-1 text-[11px]"
                      onClick={() => setEditingGoal(false)}
                    >
                      Cancel
                    </button>
                  </div>
                )}
                <div className="flex items-end gap-1 mt-2 h-8">
                  {Object.entries(steps.daily_steps)
                    .slice(-7)
                    .map(([day, count]) => {
                      const maxVal = Math.max(...Object.values(steps.daily_steps), 1);
                      const h = Math.max(4, Math.round((count / maxVal) * 100));
                      return (
                        <div
                          key={day}
                          className="w-3.5 rounded-t bg-purple-500/60 hover:bg-purple-400 transition-all"
                          style={{ height: `${h}%` }}
                          title={`${day}: ${count.toLocaleString()} steps`}
                        />
                      );
                    })}
                </div>
              </>
            ) : (
              <p className="text-sm text-slate-500 mt-1">
                {steps
                  ? 'No pedometer reading yet today — open the app on your phone to sync.'
                  : 'No pedometer data. Steps sync from your phone\u2019s hardware step counter when the app is open.'}
              </p>
            )}
          </div>
        </div>

        <AchievementsPanel userId={currentUsername || undefined} />

        <FamilyActivityCard />

        {/* Trends + LLM Analysis (on request only) */}
        <div className="lg:col-span-2 glass-panel p-5 rounded-2xl border border-white/5">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-1.5 text-[11px] text-slate-400 uppercase tracking-wider font-semibold">
              <TrendingUp size={13} className="text-indigo-400" />
              7-Day Activity Trends
            </div>
            <button
              onClick={() => void fetchTrends(Boolean(trends))}
              disabled={trendsLoading}
              data-testid="analyze-activity"
              className="glass-button flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-semibold text-slate-300 hover:text-white rounded-lg"
              title="Fitness analysis runs only when you request it"
            >
              <RefreshCw size={11} className={trendsLoading ? 'animate-spin text-indigo-400' : ''} />
              {trends ? 'Re-analyze' : 'Analyze activity'}
            </button>
          </div>

          {trendsLoading && !trends ? (
            <div className="py-6 text-center text-sm text-slate-500">Analyzing your activity on request...</div>
          ) : trends ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="p-2.5 rounded-xl bg-white/[0.03] border border-white/5">
                  <span className="text-[10px] text-slate-400 uppercase font-semibold">Steps Avg/Day</span>
                  <p className="text-base font-bold text-purple-300 mt-0.5">
                    {(trends.steps_avg ?? trends.steps_average) != null
                      ? (trends.steps_avg ?? trends.steps_average)!.toLocaleString()
                      : '—'}
                  </p>
                </div>
                <div className="p-2.5 rounded-xl bg-white/[0.03] border border-white/5">
                  <span className="text-[10px] text-slate-400 uppercase font-semibold">Workouts</span>
                  <p className="text-base font-bold text-emerald-300 mt-0.5">{trends.workout_count}</p>
                </div>
                <div className="p-2.5 rounded-xl bg-white/[0.03] border border-white/5">
                  <span className="text-[10px] text-slate-400 uppercase font-semibold">Workout Miles</span>
                  <p className="text-base font-bold text-sky-300 mt-0.5">{(trends.workout_distance_miles || 0).toFixed(1)}</p>
                </div>
                <div className="p-2.5 rounded-xl bg-white/[0.03] border border-white/5">
                  <span className="text-[10px] text-slate-400 uppercase font-semibold">Drive Cost</span>
                  <p className="text-base font-bold text-amber-300 mt-0.5">${(trends.drive_cost_usd || 0).toFixed(2)}</p>
                </div>
              </div>

              {trendsAnalysis ? (
                <div className="p-3 rounded-xl bg-indigo-500/10 border border-indigo-500/20">
                  <div className="flex items-center gap-1.5 text-[11px] font-semibold text-indigo-300 uppercase tracking-wider mb-1">
                    <span className="inline-flex items-center justify-center w-4 h-4 rounded bg-indigo-500/30 border border-indigo-400/40">
                      <Sparkles size={10} className="text-indigo-300" />
                    </span>
                    AI Insight
                  </div>
                  <p className="text-xs text-slate-300 leading-relaxed whitespace-pre-wrap">{trendsAnalysis}</p>
                </div>
              ) : (
                <div className="p-3 rounded-xl bg-white/[0.02] border border-white/5 text-[11px] text-slate-500">
                  {trendsLoading
                    ? 'Analyzing your activity with AI...'
                    : 'AI analysis unavailable right now — stats above are live from your recorded data.'}
                </div>
              )}
            </div>
          ) : (
            <div className="py-6 text-center space-y-2" data-testid="analysis-opt-in">
              <p className="text-sm text-slate-500">
                Fitness and health analysis only runs when you ask for it.
              </p>
              <p className="text-xs text-slate-600">
                Steps and workouts below are recorded data — no analysis is generated until you request it.
              </p>
              <button
                type="button"
                onClick={() => void fetchTrends(false)}
                disabled={trendsLoading}
                className="glass-button px-3 py-1.5 text-xs font-semibold text-slate-200 rounded-lg"
              >
                Analyze my activity
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Workouts Section */}
      <div className="space-y-3">
        <div className="flex items-center justify-between px-1">
          <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
            <Activity size={16} className="text-emerald-400" />
            Workouts
          </h2>
          <span className="text-xs text-slate-500">Track walks, runs, rides — steps auto-counted</span>
        </div>

        {/* Start / Stop Controls */}
        <div className="glass-panel p-4 rounded-2xl border border-white/5">
          {activeWorkout ? (
            <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <span className="relative flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500" />
                </span>
                <div>
                  <p className="text-sm font-semibold text-white flex items-center gap-2">
                    {(() => {
                      const Icon = ACTIVITY_ICONS[activeWorkout.activity_type] || Footprints;
                      return <Icon size={16} className="text-emerald-400" />;
                    })()}
                    {ACTIVITY_LABELS[activeWorkout.activity_type] || activeWorkout.activity_type} in progress
                  </p>
                  <p className="text-[11px] text-slate-400">
                    Started {new Date(activeWorkout.start_time * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
                    {' · '}
                    elapsed {formatDuration(Math.max(0, nowSeconds - activeWorkout.start_time))}
                  </p>
                </div>
              </div>
              <button
                onClick={handleStopWorkout}
                className="flex items-center gap-2 px-4 py-2 rounded-xl bg-rose-600/90 hover:bg-rose-500 text-white text-xs font-bold transition-all shadow-lg shadow-rose-600/30"
              >
                <Square size={13} />
                Stop & Save
              </button>
            </div>
          ) : (
            <div>
              <p className="text-[11px] text-slate-400 mb-2.5 flex items-center gap-1.5">
                <Play size={11} className="text-emerald-400" />
                Start tracking an activity — distance and speed come from GPS breadcrumbs.
              </p>
              <div className="flex flex-wrap gap-2">
                {WORKOUT_OPTIONS.map((opt) => {
                  const Icon = opt.icon;
                  return (
                    <button
                      key={opt.type}
                      onClick={() => handleStartWorkout(opt.type)}
                      className={`flex items-center gap-1.5 px-3 py-2 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] border border-white/10 hover:border-emerald-500/40 text-xs font-semibold text-slate-200 transition-all ${opt.color}`}
                    >
                      <Icon size={14} />
                      {opt.label}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Workout History */}
        {workouts.length > 0 ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {workouts.map((workout) => {
              const Icon = ACTIVITY_ICONS[workout.activity_type] || Footprints;
              return (
                <div key={workout.id} className="glass-card p-4 rounded-2xl border border-white/5 shadow-lg">
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="flex items-center gap-2.5">
                      <div className="p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
                        <Icon size={16} className="text-emerald-400" />
                      </div>
                      <div>
                        <p className="text-sm font-semibold text-slate-100">
                          {ACTIVITY_LABELS[workout.activity_type] || workout.activity_type}
                        </p>
                        <p className="text-[11px] text-slate-500 flex items-center gap-1">
                          <CalendarIcon size={10} />
                          {formatTimeRange(workout.start_time, workout.end_time || workout.start_time)}
                        </p>
                      </div>
                    </div>
                    {workout.steps != null && (
                      <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-purple-500/10 border border-purple-500/20 text-[11px] font-semibold text-purple-300">
                        <Footprints size={11} />
                        {workout.steps.toLocaleString()}
                        {workout.steps_source === 'gps_estimate' && (
                          <span className="text-[9px] text-slate-500 font-normal">est</span>
                        )}
                      </div>
                    )}
                  </div>

                  <div className="grid grid-cols-3 gap-2 text-center pt-1">
                    <div className="p-1.5 rounded-lg bg-white/[0.02]">
                      <span className="text-[9px] text-slate-500 uppercase">Distance</span>
                      <p className="text-xs font-bold text-white">{(workout.distance_miles || 0).toFixed(2)} mi</p>
                    </div>
                    <div className="p-1.5 rounded-lg bg-white/[0.02]">
                      <span className="text-[9px] text-slate-500 uppercase">Duration</span>
                      <p className="text-xs font-bold text-white">{formatDuration(workout.duration_seconds || 0)}</p>
                    </div>
                    <div className="p-1.5 rounded-lg bg-white/[0.02]">
                      <span className="text-[9px] text-slate-500 uppercase">Avg Pace</span>
                      <p className="text-xs font-bold text-white">
                        {workout.avg_speed_mph != null ? `${workout.avg_speed_mph} mph` : '—'}
                      </p>
                    </div>
                  </div>

                  {workout.notes && <p className="text-[11px] text-slate-400 mt-2 italic">{workout.notes}</p>}

                  <RoutePreview
                    id={workout.id}
                    completed={workout.status === 'completed'}
                    points={routePoints[workout.id]}
                    loadRoute={loadRoute}
                    fetcher={() => api.getWorkoutRoute(workout.id).then((r) => r.points)}
                  />
                </div>
              );
            })}
          </div>
        ) : (
          !activeWorkout && (
            <div className="glass-panel p-6 rounded-2xl border border-white/5 text-center">
              <p className="text-sm text-slate-400">No workouts recorded yet.</p>
              <p className="text-xs text-slate-500 mt-1">Start one above — walks and runs also estimate steps from GPS when your phone&apos;s pedometer isn&apos;t available.</p>
            </div>
          )
        )}
      </div>
    </div>
  );
};

export default Health;
