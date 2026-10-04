import { useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Activity, Flame, Mountain } from 'lucide-react';
import type { IWidgetProps } from '../../types/widget';
import { useWidgetStore } from '../../stores/widgetStore';
import { themeRegistry } from '../../themes';
import { useActiveThemeId } from '../../themes/siteTheme';
import { WidgetCard } from './WidgetCard';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useHaptics } from '../../hooks/useHaptics';
import { syncAdvice, syncStatus } from '../../lib/healthMetrics';
import { pedometerQueryOptions, stepsQueryKey } from '../../lib/healthQueries';
import ActivityRings, { type RingInput } from '../health/ActivityRings';
import { heroInsight } from '../../lib/healthRanges';

export interface HealthActivityConfig {
  themeId?: string;
  stepGoal?: number;
  floorGoal?: number;
  activeMinuteGoal?: number;
  metrics?: string[];
  steps?: number;
  floors?: number;
  activeMinutes?: number;
  calories?: number;
  /** Resolved theme colors published for the native Android widget. */
  themeAccent?: string;
  themeAccentText?: string;
}

const DEFAULT_CONFIG: Required<Pick<HealthActivityConfig, 'stepGoal'>> = {
  stepGoal: 10000,
};

/**
 * Ring diameter in the widget cell. The Health page keeps the page-sized ring;
 * this is the compact form of the same component, not a second implementation.
 *
 * Measured, not guessed: a widget cell is a ~280px column, and the ring plus its
 * zone label has to sit beside the step count without pushing the card wider
 * than its own clientWidth. 84px overflowed by 24px; 72 fits.
 */
const WIDGET_RING_SIZE = 72;

/**
 * Health / Steps / Workout dashboard widget.
 * Visual style comes exclusively from theme packages (themeRegistry).
 */
const HealthActivityWidget = ({ settingsButton, userSettings }: IWidgetProps) => {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { trigger } = useHaptics();
  const currentUsername = (user?.username || '').toLowerCase() || undefined;
  const updateWidgetConfig = useWidgetStore((s) => s.updateWidgetConfig);
  const config = (userSettings.config ?? {}) as HealthActivityConfig;

  // Jarvis-wide theme: widgets follow the one active theme, never their own.
  const themeId = useActiveThemeId();
  const theme = useMemo(() => themeRegistry.resolveTheme(themeId), [themeId]);
  const cssVars = useMemo(() => themeRegistry.cssVarsFor(themeId), [themeId]);

  // Shares one cache entry with the Health page, so the card and the page can
  // never show different numbers for the same day. Previously this used a
  // separate `['daily-steps','health-widget']` key while the page used none,
  // so the two screens genuinely could disagree.
  const stepsQuery = useQuery({
    queryKey: stepsQueryKey(currentUsername),
    queryFn: () => api.getDailySteps(currentUsername, 7),
    ...pedometerQueryOptions,
  });
  const stepsData = stepsQuery.data ?? null;
  const hasStepData = stepsData != null && Object.keys(stepsData.daily_steps ?? {}).length > 0;
  const steps = hasStepData ? (stepsData?.today ?? 0) : null;
  const stepsGoal = stepsData?.goal || config.stepGoal || DEFAULT_CONFIG.stepGoal;

  // The user's own median, so the ring can say "on your usual pace" rather than
  // just how far through an arbitrary goal the day is. Shares one cache entry with
  // the Health page, so the widget and the page cannot disagree.
  const weekQuery = useQuery({
    queryKey: ['step-ranges', 'W', currentUsername],
    queryFn: () => api.getStepRanges(currentUsername, 'W'),
    staleTime: 60_000,
  });
  const baseline = weekQuery.data?.baseline ?? null;
  const insight = heroInsight(weekQuery.data ?? null, steps ?? 0, stepsGoal);
  const rings: RingInput[] = [
    {
      id: 'steps',
      label: 'Steps',
      actual: steps ?? 0,
      goal: stepsGoal,
      unit: 'steps',
      // `tone` is deliberately not set. It overrides the zone outright, so
      // pinning it to 'on' would paint every day as "On your usual pace" — a
      // confident, wrong claim. The zone colours are the shared ones the Health
      // page uses, and matching the site theme is worth less than being right.
    },
  ];

  // Real derived metrics — no data source is invented to fill a tile.
  const weekValues = useMemo(
    () => Object.values(stepsData?.daily_steps ?? {}).filter((v): v is number => typeof v === 'number' && v > 0),
    [stepsData]
  );
  const weekAvg = weekValues.length
    ? Math.round(weekValues.reduce((a, b) => a + b, 0) / weekValues.length)
    : null;
  const weekBest = weekValues.length ? Math.max(...weekValues) : null;
  // Distance estimate from stride length, labelled "est." wherever it is shown.
  const weekMiles = weekValues.length
    ? (weekValues.reduce((a, b) => a + b, 0) * 0.7) / 1609.34
    : null;

  // Publish the resolved theme colors so the native Android home-screen widget
  // can tint itself the same way. Keeps palettes in the pack data (never
  // hardcoded in the Java widget) and keeps both surfaces in sync.
  const accent = theme.tokens.accent;
  const accentText = theme.tokens.text;
  useEffect(() => {
    if (config.themeAccent !== accent || config.themeAccentText !== accentText) {
      void updateWidgetConfig(userSettings.widget_key, {
        themeAccent: accent,
        themeAccentText: accentText,
      });
    }
  }, [accent, accentText, config.themeAccent, config.themeAccentText, updateWidgetConfig, userSettings.widget_key]);

  const track = cssVars['--ht-progress-track'] ?? theme.tokens.border;
  const glow = theme.tokens.glow;

  // A number with no age is a number you cannot trust. This is what lets the
  // card distinguish "12,000 steps so far today" from "12,000 steps, and the
  // phone stopped reporting two hours ago" -- otherwise a dead sensor and a
  // quiet day look identical here.
  const sync = syncStatus(stepsData?.last_synced);
  const syncHint = syncAdvice(sync);

  const open = () => {
    trigger('light');
    navigate('/fitness');
  };

  return (
    <WidgetCard
      title="Health"
      settingsButton={settingsButton}
      accentColor={theme.tokens.accent}
      expandedClassName="bg-black/80"
    >
      <div
        data-theme-id={theme.id}
        data-testid="health-activity-widget"
        role="button"
        tabIndex={0}
        onClick={open}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            open();
          }
        }}
        className="h-full flex flex-col gap-3 p-1 rounded-xl relative overflow-hidden cursor-pointer focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 pointer-coarse:min-h-11"
        style={{
          ...cssVars,
          background: cssVars['--ht-bg'],
          color: cssVars['--ht-text'],
          borderRadius: cssVars['--ht-radius'],
          border: `1px solid ${cssVars['--ht-border']}`,
          fontFamily: cssVars['--ht-font'] || undefined,
          boxShadow: glow ? `0 0 24px ${glow}33` : undefined,
          ...(theme.tokens.showCornerCut
            ? { clipPath: 'polygon(0 0, calc(100% - 14px) 0, 100% 14px, 100% 100%, 14px 100%, 0 calc(100% - 14px))' }
            : {}),
        }}
      >
        {theme.tokens.motif === 'petal' && (
          <svg
            aria-hidden
            viewBox="0 0 120 120"
            className="pointer-events-none absolute -right-3 -top-3 h-20 w-20 opacity-60"
            style={{ color: cssVars['--ht-accent-alt'] }}
          >
            <g fill="none" stroke="currentColor" strokeWidth="1.6" strokeOpacity="0.5">
              <path d="M30 22c8 6 8 16 0 22-8-6-8-16 0-22z" />
              <path d="M58 60c7 5 7 14 0 19-7-5-7-14 0-19z" />
              <circle cx="72" cy="30" r="3.2" />
            </g>
          </svg>
        )}

        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="text-xs uppercase tracking-wider" style={{ color: cssVars['--ht-text-muted'] }}>
              Today
            </div>
            <div
              className="text-2xl font-bold tabular-nums"
              style={{
                fontFamily: cssVars['--ht-number-font'] || undefined,
                color: cssVars['--ht-text'],
              }}
            >
              {steps == null ? '—' : steps.toLocaleString()}
              <span className="text-sm font-medium ml-1" style={{ color: cssVars['--ht-text-muted'] }}>
                / {stepsGoal.toLocaleString()}
              </span>
            </div>
            {/* Freshness, or an explicit reason the number may be frozen. */}
            {(sync.label || syncHint) && (
              <div
                data-testid="health-sync-status"
                data-freshness={sync.freshness}
                className="text-[10px] leading-tight mt-0.5"
                style={{ color: sync.stale ? cssVars['--ht-warning'] ?? '#f59e0b' : cssVars['--ht-text-muted'] }}
              >
                {syncHint ?? `Updated ${sync.label}`}
              </div>
            )}
          </div>
          {/* Not `shrink-0`: ActivityRings carries a full-width note when the
              baseline is thin, and a wrapper that refuses to shrink makes the
              card overflow its own column on a phone. */}
          <div className="min-w-0 shrink">
            <ActivityRings
              rings={rings}
              baseline={baseline}
              thin={weekQuery.data?.thin}
              baselineMinDays={weekQuery.data?.baseline_min_days}
              size={WIDGET_RING_SIZE}
              // The insight line below already explains a thin baseline, so the
              // ring does not repeat it.
              showThinNote={false}
            />
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2">
          {[
            {
              key: 'avg',
              label: '7-day avg',
              value: weekAvg == null ? null : weekAvg.toLocaleString(),
              pct: weekAvg == null ? 0 : weekAvg / Math.max(1, stepsGoal),
              icon: Activity,
              ring: cssVars['--ht-ring-2'] ?? cssVars['--ht-accent'],
            },
            {
              key: 'best',
              label: 'Best day',
              value: weekBest == null ? null : weekBest.toLocaleString(),
              pct: weekBest == null ? 0 : weekBest / Math.max(1, stepsGoal),
              icon: Mountain,
              ring: cssVars['--ht-ring-3'] ?? cssVars['--ht-progress'],
            },
            {
              key: 'distance',
              label: 'Miles (est.)',
              value: weekMiles == null ? null : weekMiles.toFixed(1),
              pct: weekMiles == null ? 0 : weekMiles / 10,
              icon: Flame,
              ring: cssVars['--ht-accent'],
            },
          ].map((tile) => {
            const Icon = tile.icon;
            return (
              <div
                key={tile.key}
                className="rounded-lg p-2 flex flex-col items-center gap-1"
                style={{
                  background: cssVars['--ht-surface'],
                  borderRadius: `calc(${cssVars['--ht-radius']}px * 0.65)`,
                  border: `1px solid ${cssVars['--ht-border']}`,
                }}
              >
                <Icon size={14} style={{ color: cssVars['--ht-text-muted'] }} aria-hidden />
                <div
                  className="text-lg font-semibold tabular-nums leading-none"
                  style={{
                    fontFamily: cssVars['--ht-number-font'] || undefined,
                    color: cssVars['--ht-text'],
                  }}
                >
                  {tile.value == null ? '—' : tile.value}
                </div>
                <div className="text-[10px]" style={{ color: cssVars['--ht-text-muted'] }}>
                  {tile.label}
                </div>
                <div
                  className="w-full h-1 rounded-full overflow-hidden"
                  style={{ background: track }}
                >
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.min(100, Math.round(tile.pct * 100))}%`,
                      background: tile.ring,
                      boxShadow: glow ? `0 0 6px ${glow}` : undefined,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>

        <div
          className="text-[11px] text-center leading-tight"
          style={{ color: cssVars['--ht-text-muted'] }}
        >
          {insight ? (
            <>
              <span
                data-testid="health-activity-insight"
                className="font-semibold"
                style={{ color: cssVars['--ht-accent'] }}
              >
                {insight.headline}
              </span>
              <span className="block">{insight.detail}</span>
            </>
          ) : (
            /* No insight is not an empty box: say what is missing and why, rather
               than rendering a panel that just looks broken. */
            <span data-testid="health-activity-no-insight">
              {steps == null || steps === 0
                ? 'Nothing recorded yet. Walk a little and this fills in.'
                : 'Not enough history yet for a fair comparison.'}
            </span>
          )}
        </div>
      </div>
    </WidgetCard>
  );
};

export default HealthActivityWidget;
