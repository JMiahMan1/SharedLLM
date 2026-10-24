import { useMemo, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Droplets, HeartPulse, Moon, Sun, Utensils, Zap } from 'lucide-react';
import type { IWidgetProps } from '../../types/widget';
import { WidgetCard } from './WidgetCard';
import { api } from '../../services/api';
import { formatMinutes, localDay, scoreTone } from '../../lib/healthHub';
import { isKidMode, metricOn } from '../../lib/healthSwitches';
import { activeMinutesLine } from '../../lib/activeMinutes';
import { readMealPlan } from '../../lib/mealPlanStore';
import { planDayOf } from '../../lib/mealPlan';

/**
 * The Health hub's today card on the dashboard (Phase 8): readiness, last
 * night, water, food, the week's active minutes and tonight's planned dinner.
 *
 * Steps stay on the Health widget (`health_activity`); this card never reads
 * step data, so the dashboard still has one place for steps. It uses the same
 * queries and switches as the Health page, so the two cannot disagree, and
 * kid mode shows what was logged rather than calories.
 */
export default function HealthTodayWidget({ settingsButton }: IWidgetProps) {
  const navigate = useNavigate();
  const day = useMemo(() => localDay(), []);
  const summaryQuery = useQuery({
    queryKey: ['health-summary', day],
    queryFn: () => api.getHealthSummary(day),
    staleTime: 60_000,
  });
  const summary = summaryQuery.data;
  const on = (key: Parameters<typeof metricOn>[1]) => metricOn(summary?.profile, key);
  const activeQuery = useQuery({
    queryKey: ['health-active-minutes', day],
    queryFn: () => api.getHealthActiveMinutes(day),
    staleTime: 120_000,
    enabled: Boolean(summary) && on('active_minutes'),
  });
  const planQuery = useQuery({
    queryKey: ['meal-plan'],
    queryFn: () => readMealPlan(),
    staleTime: 300_000,
    retry: false,
  });

  const kid = isKidMode(summary?.profile);
  const tonight = planQuery.data?.plan[planDayOf()] ?? null;
  const readiness = summary?.readiness ?? null;
  const tone = scoreTone(readiness?.score);

  const rows: Array<{ key: string; icon: ReactNode; text: string }> = [];
  if (summary) {
    if (on('sleep')) {
      const night = summary.sleep.last_night;
      rows.push({
        key: 'sleep',
        icon: <Moon size={13} className="text-sky-300" />,
        text: night ? `Slept ${formatMinutes(night.duration_minutes)}${summary.sleep.score ? ` · ${summary.sleep.score.score}/100` : ''}` : 'No sleep logged for last night',
      });
    }
    if (on('water')) {
      rows.push({
        key: 'water',
        icon: <Droplets size={13} className="text-sky-300" />,
        text: summary.water.logged ? `${summary.water.ml.toLocaleString()} of ${summary.water.goal_ml.toLocaleString()} ml water` : 'No water logged yet',
      });
    }
    if (on('nutrition')) {
      const food = summary.nutrition;
      rows.push({
        key: 'food',
        icon: <Utensils size={13} className="text-emerald-300" />,
        text: !food.logged
          ? 'No food logged yet'
          : kid
            ? `${food.entries_count} ${food.entries_count === 1 ? 'thing' : 'things'} logged`
            : `${Math.round(food.totals.calories).toLocaleString()} kcal logged`,
      });
    }
    if (on('active_minutes') && activeQuery.data) {
      rows.push({ key: 'active', icon: <Zap size={13} className="text-amber-300" />, text: activeMinutesLine(activeQuery.data) });
    }
  }

  return (
    <WidgetCard
      title="Health today"
      icon={<HeartPulse size={16} />}
      settingsButton={settingsButton}
      isLoading={summaryQuery.isLoading}
      error={summaryQuery.isError ? 'The health store could not be reached.' : null}
      onRetry={() => void summaryQuery.refetch()}
    >
      <button
        type="button"
        onClick={() => navigate('/fitness')}
        data-testid="health-today-widget"
        className="flex h-full w-full flex-col gap-2 rounded-xl p-1 text-left pointer-coarse:min-h-11"
      >
        {summary && on('readiness') && (
          <div className="flex items-center gap-2">
            <Sun size={14} className="text-amber-300" />
            {readiness ? (
              <span className={`text-sm font-semibold ${tone.text}`}>
                Readiness {readiness.score} · <span className="capitalize">{readiness.label}</span>
              </span>
            ) : (
              <span className="text-xs text-slate-400">No readiness yet: log sleep or the check-in.</span>
            )}
          </div>
        )}
        <ul className="space-y-1.5 text-xs text-slate-300">
          {rows.map((row) => (
            <li key={row.key} className="flex items-center gap-2">
              {row.icon}
              <span className="min-w-0 truncate">{row.text}</span>
            </li>
          ))}
          {on('nutrition') && planQuery.isSuccess && (
            <li className="flex items-center gap-2" data-testid="health-today-dinner">
              <Utensils size={13} className="text-purple-300" />
              <span className="min-w-0 truncate">
                {tonight?.title ? `Tonight: ${tonight.title}${tonight.cooked ? ' (cooked)' : ''}` : 'No dinner planned for tonight'}
              </span>
            </li>
          )}
        </ul>
      </button>
    </WidgetCard>
  );
}
