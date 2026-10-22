import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  ChevronRight,
  Droplets,
  Footprints,
  Loader2,
  Moon,
  Sparkles,
  Sun,
  Utensils,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import {
  calorieSummary,
  confidenceLabel,
  formatDayLabel,
  formatMinutes,
  localDay,
  nutritionProgress,
  scoreTone,
  sleepWindowLabel,
  targetLabel,
} from '../../lib/healthHub';
import { stepsSummary } from '../../lib/healthMetrics';
import { pedometerQueryOptions, stepsQueryKey } from '../../lib/healthQueries';
import type { HealthTab } from './HealthTabs';

interface Props {
  onNavigate: (tab: HealthTab) => void;
}

const MOOD_WORDS = ['', 'Rough', 'Low', 'Okay', 'Good', 'Great'];
const SORE_WORDS = ['', 'None', 'Mild', 'Some', 'Sore', 'Very sore'];

function ScaleRow({
  label,
  value,
  onChange,
  words,
  testId,
}: {
  label: string;
  value: number | null;
  onChange: (value: number) => void;
  words: string[];
  testId: string;
}) {
  return (
    <div className="flex items-center justify-between gap-2" data-testid={testId}>
      <span className="text-xs text-slate-400">{label}</span>
      <div className="flex gap-1" role="group" aria-label={label}>
        {[1, 2, 3, 4, 5].map((score) => (
          <button
            key={score}
            type="button"
            aria-pressed={value === score}
            aria-label={`${label}: ${words[score]}`}
            onClick={() => onChange(score)}
            className={`h-9 min-w-9 rounded-lg border px-1 text-[11px] font-semibold transition-colors pointer-coarse:h-11 pointer-coarse:min-w-11 ${
              value === score
                ? 'border-purple-500/40 bg-purple-500/20 text-purple-200'
                : 'border-white/10 bg-white/[0.03] text-slate-400 hover:text-white'
            }`}
          >
            {score}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * The Today screen: one interpreted score, then the day's signals.
 *
 * Order matters. Readiness comes first because it answers "how should today
 * go?", then sleep, food, water and steps give the evidence. Scores that need
 * data the family does not have yet say so and offer the manual path, rather
 * than showing a confident guess.
 */
export default function HealthToday({ onNavigate }: Props) {
  const queryClient = useQueryClient();
  const day = useMemo(() => localDay(), []);
  const [draft, setDraft] = useState<{
    energy?: number | null;
    mood?: number | null;
    soreness?: number | null;
  }>({});
  const [waterDraft, setWaterDraft] = useState('');

  const summaryQuery = useQuery({
    queryKey: ['health-summary', day],
    queryFn: () => api.getHealthSummary(day),
    staleTime: 60_000,
  });
  const stepsQuery = useQuery({
    queryKey: stepsQueryKey(),
    queryFn: () => api.getDailySteps(undefined, 7),
    ...pedometerQueryOptions,
  });
  const familyQuery = useQuery({
    queryKey: ['health-family', day],
    queryFn: () => api.getHealthFamily(day),
    staleTime: 120_000,
  });

  const summary = summaryQuery.data;
  const steps = stepsSummary(stepsQuery.data);
  const checkin = summary?.checkin ?? null;
  // A saved check-in is the starting point, but the buttons stay editable
  // without an effect copying server state into local state on every refetch.
  const energy = draft.energy !== undefined ? draft.energy : (checkin?.energy ?? null);
  const mood = draft.mood !== undefined ? draft.mood : (checkin?.mood ?? null);
  const soreness = draft.soreness !== undefined ? draft.soreness : (checkin?.soreness ?? null);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['health-summary', day] });
    void queryClient.invalidateQueries({ queryKey: ['health-checkins'] });
  };

  const saveCheckin = useMutation({
    mutationFn: () =>
      api.saveCheckin(day, {
        ...(energy !== null ? { energy } : {}),
        ...(mood !== null ? { mood } : {}),
        ...(soreness !== null ? { soreness } : {}),
      }),
    onSuccess: () => {
      toast.success('Check-in saved');
      setDraft({});
      invalidate();
    },
    onError: (error: Error) => toast.error(error.message || 'Could not save the check-in'),
  });

  const addWater = useMutation({
    mutationFn: (ml: number) =>
      api.addHealthMetrics([
        {
          metric: 'water',
          value: ml,
          unit: 'ml',
          recorded_at: new Date().toISOString(),
          source: 'manual',
        },
      ]),
    onSuccess: () => {
      invalidate();
    },
    onError: (error: Error) => toast.error(error.message || 'Could not log water'),
  });

  if (summaryQuery.isLoading) {
    return (
      <div className="space-y-3" aria-hidden>
        <div className="h-40 skeleton rounded-2xl" />
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="h-36 skeleton rounded-2xl" />
          <div className="h-36 skeleton rounded-2xl" />
        </div>
      </div>
    );
  }

  if (summaryQuery.isError || !summary) {
    return (
      <div className="glass-panel rounded-2xl border border-white/5 p-6 text-center" data-testid="health-today-error">
        <AlertCircle className="mx-auto text-amber-300" size={22} />
        <p className="mt-2 text-sm text-slate-300">The health store could not be reached.</p>
        <button
          type="button"
          onClick={() => summaryQuery.refetch()}
          className="glass-button mt-3 min-h-11 px-4 text-xs"
        >
          Try again
        </button>
      </div>
    );
  }

  const readiness = summary.readiness;
  const readinessTone = scoreTone(readiness?.score);
  const sleep = summary.sleep;
  const sleepTone = scoreTone(sleep.score?.score);
  const nutrition = summary.nutrition;
  const nutritionTarget = nutrition.target;
  const nutritionProgressValue = nutritionProgress(nutrition.totals.calories, nutritionTarget);
  const water = summary.water;

  return (
    <div className="space-y-3 sm:space-y-4" data-testid="health-today">
      <p className="px-1 text-[11px] text-slate-500">{formatDayLabel(day)}</p>

      {!summary.profile.exists && (
        <button
          type="button"
          onClick={() => onNavigate('health')}
          data-testid="health-profile-prompt"
          className="glass-panel flex w-full items-center gap-3 rounded-2xl border border-purple-500/20 bg-purple-500/[0.06] p-4 text-left"
        >
          <Sparkles className="shrink-0 text-purple-300" size={20} />
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold text-white">Set up your health profile</span>
            <span className="mt-0.5 block text-xs text-slate-400">
              Age, height and sex turn sleep and food logs into a range that fits you. Nothing is
              guessed until you do.
            </span>
          </span>
          <ChevronRight className="shrink-0 text-slate-500" size={18} />
        </button>
      )}

      {/* Readiness: the one interpreting score. */}
      <section
        className={`glass-panel rounded-2xl border p-4 sm:p-5 ${readiness ? readinessTone.border : 'border-white/5'}`}
        aria-labelledby="readiness-heading"
        data-testid="readiness-card"
      >
        <div className="flex items-center justify-between gap-3">
          <h2
            id="readiness-heading"
            className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400"
          >
            <Sun size={13} className="text-amber-300" />
            Readiness
          </h2>
          {readiness && (
            <span className="text-[10px] uppercase tracking-wider text-slate-500">
              {readiness.confidence} confidence
            </span>
          )}
        </div>

        {readiness ? (
          <div className="mt-3 flex items-center gap-4">
            <div
              className={`flex h-20 w-20 shrink-0 flex-col items-center justify-center rounded-full border ${readinessTone.border} ${readinessTone.bg}`}
            >
              <span className={`text-2xl font-bold tabular-nums ${readinessTone.text}`}>
                {readiness.score}
              </span>
              <span className="text-[9px] uppercase tracking-wider text-slate-500">of 100</span>
            </div>
            <div className="min-w-0">
              <p className={`text-lg font-bold capitalize ${readinessTone.text}`}>{readiness.label}</p>
              <p className="mt-0.5 text-xs leading-relaxed text-slate-300">{readiness.detail}</p>
              {confidenceLabel(readiness) && (
                <p className="mt-1 text-[11px] text-slate-500">{confidenceLabel(readiness)}</p>
              )}
              {readiness.missing.length > 0 && (
                <p className="mt-1 text-[11px] text-slate-500">
                  Missing: {readiness.missing.join(', ')}.
                </p>
              )}
            </div>
          </div>
        ) : (
          <div className="mt-2">
            <p className="text-sm text-slate-300">No readiness score yet.</p>
            <p className="mt-1 text-xs text-slate-500">
              Log last night&apos;s sleep or answer the check-in below. A score appears as soon as
              there is something real to interpret.
            </p>
          </div>
        )}
      </section>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {/* Sleep */}
        <section
          className="glass-panel flex flex-col rounded-2xl border border-white/5 p-4"
          aria-labelledby="today-sleep-heading"
          data-testid="today-sleep-card"
        >
          <div className="flex items-center justify-between gap-2">
            <h3
              id="today-sleep-heading"
              className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400"
            >
              <Moon size={13} className="text-sky-300" />
              Last night
            </h3>
            {sleep.score && (
              <span className={`text-xs font-bold ${sleepTone.text}`}>{sleep.score.score}/100</span>
            )}
          </div>
          {sleep.last_night ? (
            <>
              <p className="mt-2 text-2xl font-bold text-white">
                {formatMinutes(sleep.last_night.duration_minutes)}
              </p>
              <p className="text-[11px] text-slate-500">
                {sleepWindowLabel(sleep.last_night.bedtime, sleep.last_night.wake_time) ??
                  (sleep.last_night.quality ? `Felt ${sleep.last_night.quality}/5` : 'Times not recorded')}
                {' · '}
                {sleep.last_night.source === 'manual' ? 'hand logged' : sleep.last_night.source}
              </p>
              {sleep.score && (
                <p className="mt-1 text-[11px] text-slate-400">{sleep.score.label} · {sleep.score.detail}</p>
              )}
            </>
          ) : (
            <p className="mt-2 flex-1 text-xs text-slate-400">
              Nothing logged for last night.
              {sleep.need_minutes ? ` Your need is ${formatMinutes(sleep.need_minutes)}.` : ''}
            </p>
          )}
          <button
            type="button"
            onClick={() => onNavigate('sleep')}
            className="mt-3 flex min-h-10 items-center justify-between rounded-xl border border-white/10 bg-white/[0.03] px-3 text-xs font-semibold text-slate-300 hover:text-white pointer-coarse:min-h-11"
          >
            {sleep.last_night ? 'Open sleep' : 'Log sleep'}
            <ChevronRight size={14} />
          </button>
        </section>

        {/* Nutrition */}
        <section
          className="glass-panel flex flex-col rounded-2xl border border-white/5 p-4"
          aria-labelledby="today-nutrition-heading"
          data-testid="today-nutrition-card"
        >
          <div className="flex items-center justify-between gap-2">
            <h3
              id="today-nutrition-heading"
              className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400"
            >
              <Utensils size={13} className="text-emerald-300" />
              Food
            </h3>
            <span className="text-[10px] text-slate-500">{targetLabel(nutritionTarget)}</span>
          </div>
          <p className="mt-2 text-2xl font-bold text-white">
            {nutrition.logged
              ? `${Math.round(nutrition.totals.calories).toLocaleString()} kcal`
              : 'Nothing logged'}
          </p>
          <p className="text-[11px] text-slate-500">{calorieSummary(nutrition)}</p>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/[0.06]">
            <div
              className={`h-full rounded-full ${nutritionProgressValue.over ? 'bg-amber-500' : 'bg-emerald-500'}`}
              style={{ width: `${nutritionProgressValue.percent ?? 0}%` }}
            />
          </div>
          <div className="mt-2 flex gap-3 text-[10px] text-slate-500">
            <span>P {Math.round(nutrition.totals.protein_g)}g</span>
            <span>C {Math.round(nutrition.totals.carbs_g)}g</span>
            <span>F {Math.round(nutrition.totals.fat_g)}g</span>
          </div>
          <button
            type="button"
            onClick={() => onNavigate('nutrition')}
            className="mt-3 flex min-h-10 items-center justify-between rounded-xl border border-white/10 bg-white/[0.03] px-3 text-xs font-semibold text-slate-300 hover:text-white pointer-coarse:min-h-11"
          >
            {nutrition.logged ? 'Open nutrition' : 'Log food'}
            <ChevronRight size={14} />
          </button>
        </section>

        {/* Water */}
        <section
          className="glass-panel rounded-2xl border border-white/5 p-4"
          aria-labelledby="today-water-heading"
          data-testid="today-water-card"
        >
          <div className="flex items-center justify-between gap-2">
            <h3
              id="today-water-heading"
              className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400"
            >
              <Droplets size={13} className="text-sky-300" />
              Water
            </h3>
            <span className="text-[10px] text-slate-500">
              {summary.defaults_used.includes('water_goal_ml') ? 'guideline goal' : 'goal'}
            </span>
          </div>
          <p className="mt-2 text-2xl font-bold text-white">
            {water.logged ? `${water.ml.toLocaleString()} ml` : 'Nothing logged'}
          </p>
          <p className="text-[11px] text-slate-500">
            {water.goal_ml ? `of ${water.goal_ml.toLocaleString()} ml` : 'Set a goal in Health'}
          </p>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/[0.06]">
            <div
              className="h-full rounded-full bg-sky-500"
              style={{ width: `${water.percent ?? 0}%` }}
            />
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {[250, 500].map((ml) => (
              <button
                key={ml}
                type="button"
                onClick={() => addWater.mutate(ml)}
                disabled={addWater.isPending}
                data-testid={`add-water-${ml}`}
                className="min-h-10 rounded-xl border border-white/10 bg-white/[0.03] px-3 text-xs font-semibold text-slate-300 hover:text-white disabled:opacity-50 pointer-coarse:min-h-11"
              >
                +{ml} ml
              </button>
            ))}
            <div className="flex min-w-0 flex-1 gap-1">
              <input
                type="number"
                min={1}
                max={5000}
                value={waterDraft}
                onChange={(event) => setWaterDraft(event.target.value)}
                aria-label="Custom water amount in millilitres"
                placeholder="ml"
                className="glass-input h-10 w-full min-w-0 px-2 text-xs pointer-coarse:h-11"
              />
              <button
                type="button"
                onClick={() => {
                  const ml = Number(waterDraft);
                  if (!Number.isFinite(ml) || ml <= 0) {
                    toast.error('Enter an amount in millilitres');
                    return;
                  }
                  addWater.mutate(ml);
                  setWaterDraft('');
                }}
                disabled={addWater.isPending}
                className="glass-button min-h-10 px-3 text-xs pointer-coarse:min-h-11"
              >
                Add
              </button>
            </div>
          </div>
        </section>

        {/* Steps, still geo's number. */}
        <section
          className="glass-panel rounded-2xl border border-white/5 p-4"
          aria-labelledby="today-steps-heading"
          data-testid="today-steps-card"
        >
          <div className="flex items-center justify-between gap-2">
            <h3
              id="today-steps-heading"
              className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400"
            >
              <Footprints size={13} className="text-emerald-300" />
              Steps
            </h3>
            <span className="text-[10px] text-slate-500">from your phone</span>
          </div>
          <p className="mt-2 text-2xl font-bold text-white">
            {steps.hasData ? steps.today.toLocaleString() : 'No reading yet'}
          </p>
          <p className="text-[11px] text-slate-500">
            {steps.hasData ? `of ${steps.goal.toLocaleString()} goal` : 'Open the app on your phone to sync'}
          </p>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/[0.06]">
            <div
              className="h-full rounded-full bg-purple-500"
              style={{ width: `${steps.percent}%` }}
            />
          </div>
          <button
            type="button"
            onClick={() => onNavigate('fitness')}
            className="mt-3 flex min-h-10 w-full items-center justify-between rounded-xl border border-white/10 bg-white/[0.03] px-3 text-xs font-semibold text-slate-300 hover:text-white pointer-coarse:min-h-11"
          >
            Workouts and step history
            <ChevronRight size={14} />
          </button>
        </section>
      </div>

      {/* Morning check-in: the fallback when no wearable exists. */}
      <section
        className="glass-panel rounded-2xl border border-white/5 p-4"
        aria-labelledby="checkin-heading"
        data-testid="checkin-card"
      >
        <div className="flex items-center justify-between gap-3">
          <h3
            id="checkin-heading"
            className="text-[11px] font-semibold uppercase tracking-wider text-slate-400"
          >
            Morning check-in
          </h3>
          {checkin && <span className="text-[10px] text-emerald-300">Saved today</span>}
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Two taps is enough. With no watch, this is the signal readiness uses.
        </p>
        <div className="mt-3 space-y-2">
          <ScaleRow label="Energy" value={energy} onChange={(value) => setDraft((d) => ({ ...d, energy: value }))} words={MOOD_WORDS} testId="checkin-energy" />
          <ScaleRow label="Mood" value={mood} onChange={(value) => setDraft((d) => ({ ...d, mood: value }))} words={MOOD_WORDS} testId="checkin-mood" />
          <ScaleRow label="Soreness" value={soreness} onChange={(value) => setDraft((d) => ({ ...d, soreness: value }))} words={SORE_WORDS} testId="checkin-soreness" />
        </div>
        <button
          type="button"
          onClick={() => saveCheckin.mutate()}
          disabled={saveCheckin.isPending || (energy === null && mood === null && soreness === null)}
          data-testid="save-checkin"
          className="glass-button mt-3 flex min-h-11 w-full items-center justify-center gap-2 text-xs font-semibold disabled:opacity-50"
        >
          {saveCheckin.isPending ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
          {checkin ? 'Update check-in' : 'Save check-in'}
        </button>
      </section>

      {(familyQuery.data?.people.length ?? 0) > 0 && (
        <section
          className="glass-panel rounded-2xl border border-white/5 p-4"
          aria-labelledby="health-family-heading"
          data-testid="health-family-today"
        >
          <h3
            id="health-family-heading"
            className="text-[11px] font-semibold uppercase tracking-wider text-slate-400"
          >
            Shared with you
          </h3>
          <div className="mt-2 space-y-2">
            {familyQuery.data?.people.map((person) => {
              const cycle = person.cycle;
              const cycleLine =
                cycle?.enabled && cycle.phase_label
                  ? [
                      `Cycle: ${cycle.phase_label}`,
                      cycle.cycle_day !== null ? `day ${cycle.cycle_day}` : null,
                      cycle.days_until_next_period !== null
                        ? cycle.days_until_next_period >= 0
                          ? `next period in ${cycle.days_until_next_period} day${cycle.days_until_next_period === 1 ? '' : 's'}`
                          : 'period expected'
                        : null,
                    ]
                      .filter(Boolean)
                      .join(' · ')
                  : null;
              return (
                <div key={person.user_id} className="space-y-2 text-xs">
                  <div className="flex items-center justify-between gap-3">
                    <span className="capitalize text-slate-300">{person.user_id}</span>
                    <span className="text-slate-500">
                      {person.sleep?.logged && person.sleep.score !== null
                        ? `Sleep ${person.sleep.score}/100`
                        : person.nutrition?.logged
                          ? `${Math.round(person.nutrition.calories)} kcal`
                          : cycle?.enabled
                            ? 'Cycle shared'
                            : 'Nothing shared yet'}
                    </span>
                  </div>
                  {cycle?.enabled && cycleLine && (
                    <p className="text-[11px] text-slate-400">{cycleLine}</p>
                  )}
                  {cycle?.enabled && cycle.support && cycle.support.tips.length > 0 && (
                    <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                      <p className="text-[11px] font-semibold text-slate-300">
                        {cycle.support.title}
                      </p>
                      <ul className="mt-1.5 list-disc space-y-1 pl-4 text-[11px] leading-relaxed text-slate-400">
                        {cycle.support.tips.map((tip) => (
                          <li key={tip}>{tip}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
