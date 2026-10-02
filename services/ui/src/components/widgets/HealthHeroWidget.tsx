import { useQuery } from '@tanstack/react-query';
import { Footprints, Pin } from 'lucide-react';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import ActivityRings, { type RingInput } from '../health/ActivityRings';
import { heroInsight, ringSpec } from '../../lib/healthRanges';
import type { IWidgetProps } from '../../types/widget';

const DEFAULT_STEP_GOAL = 10000;

/**
 * The dashboard's health hero: today's rings plus the one thing worth saying.
 *
 * Deliberately reuses ActivityRings and heroInsight rather than growing a
 * second ring implementation for the dashboard — the Health page and this
 * widget must not be able to drift apart.
 *
 * A 7-day window, because a hero answers "how am I doing right now". The
 * longer ranges belong on the Health page, where there is room to explain them.
 */
export default function HealthHeroWidget({
  settingsButton,
  userSettings,
  onTogglePin,
}: IWidgetProps) {
  const { user } = useAuth();

  const { data: today } = useQuery({
    queryKey: ['step-ranges', 'D', user?.username],
    queryFn: () => api.getStepRanges(user?.username, 'D'),
    staleTime: 60_000,
  });

  const { data: week } = useQuery({
    queryKey: ['step-ranges', 'W', user?.username],
    queryFn: () => api.getStepRanges(user?.username, 'W'),
    staleTime: 60_000,
  });

  const steps = today?.buckets?.[0]?.steps ?? 0;
  const goal = today?.goal ?? DEFAULT_STEP_GOAL;
  const baseline = week?.baseline ?? null;
  const insight = heroInsight(week, steps, goal);
  const spec = ringSpec(steps, goal, DEFAULT_STEP_GOAL);

  const rings: RingInput[] = [
    { id: 'steps', label: 'Steps', actual: spec.actual, goal: spec.goal, unit: 'steps' },
  ];

  return (
    <div
      data-testid="health-hero-widget"
      className="flex h-full flex-col gap-3 p-4"
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-slate-200">
          <Footprints size={14} className="text-emerald-300" />
          Today
        </h3>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onTogglePin}
            aria-pressed={userSettings.is_pinned}
            aria-label={userSettings.is_pinned ? 'Unpin Today' : 'Pin Today'}
            className="rounded-lg p-1.5 text-slate-400 hover:text-white pointer-coarse:min-h-11 pointer-coarse:min-w-11 flex items-center justify-center"
          >
            <Pin size={13} className={userSettings.is_pinned ? 'text-purple-300' : ''} />
          </button>
          {settingsButton}
        </div>
      </div>

      <div className="flex items-center gap-4">
        <ActivityRings
          rings={rings}
          baseline={baseline}
          thin={week?.thin}
          baselineMinDays={week?.baseline_min_days}
        />
        <div className="min-w-0 flex-1">
          {insight ? (
            <>
              <p
                data-testid="health-hero-insight"
                className="text-sm font-semibold text-slate-100"
              >
                {insight.headline}
              </p>
              <p className="mt-0.5 text-[11px] text-slate-400">{insight.detail}</p>
            </>
          ) : (
            /* No insight is not an empty box: say what is missing and why,
               rather than rendering a panel that looks broken. */
            <p data-testid="health-hero-no-insight" className="text-[11px] text-slate-400">
              {steps > 0
                ? 'No reading today yet.'
                : 'Nothing recorded yet. Walk a little and this fills in.'}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
