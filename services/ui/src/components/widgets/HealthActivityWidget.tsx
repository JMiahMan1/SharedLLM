import { useMemo, useState } from 'react';
import { Footprints, Flame, Mountain, Timer } from 'lucide-react';
import type { IWidgetProps } from '../../types/widget';
import { useWidgetStore } from '../../stores/widgetStore';
import { themeRegistry } from '../../themes';
import { ThemePackageManager } from '../../themes/ThemePackageManager';
import { WidgetCard } from './WidgetCard';

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
}

const DEFAULT_CONFIG: Required<Pick<HealthActivityConfig, 'stepGoal' | 'floorGoal' | 'activeMinuteGoal'>> = {
  stepGoal: 10000,
  floorGoal: 10,
  activeMinuteGoal: 30,
};

/** Demo-safe values until pedometer / HA sensors are wired. */
function readMetrics(config: HealthActivityConfig) {
  return {
    steps: Number(config.steps ?? 6420),
    floors: Number(config.floors ?? 4),
    activeMinutes: Number(config.activeMinutes ?? 22),
    calories: Number(config.calories ?? 318),
  };
}

function Ring({
  pct,
  stroke,
  track,
  size = 96,
  children,
}: {
  pct: number;
  stroke: string;
  track: string;
  size?: number;
  children: React.ReactNode;
}) {
  const r = (size - 10) / 2;
  const c = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(1, pct));
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={track} strokeWidth="8" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={stroke}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - clamped)}
          style={{ transition: 'stroke-dashoffset 0.4s ease' }}
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">{children}</div>
    </div>
  );
}

/**
 * Health / Steps / Workout dashboard widget.
 * Visual style comes exclusively from theme packages (themeRegistry).
 */
const HealthActivityWidget = ({ settingsButton, userSettings }: IWidgetProps) => {
  const updateWidgetConfig = useWidgetStore((s) => s.updateWidgetConfig);
  const config = (userSettings.config ?? {}) as HealthActivityConfig;
  const [showThemes, setShowThemes] = useState(false);

  const themeId = config.themeId ?? 'aurora';
  const theme = useMemo(() => themeRegistry.resolveTheme(themeId), [themeId]);
  const cssVars = useMemo(() => themeRegistry.cssVarsFor(themeId), [themeId]);

  const goals = {
    stepGoal: config.stepGoal ?? DEFAULT_CONFIG.stepGoal,
    floorGoal: config.floorGoal ?? DEFAULT_CONFIG.floorGoal,
    activeMinuteGoal: config.activeMinuteGoal ?? DEFAULT_CONFIG.activeMinuteGoal,
  };
  const metrics = readMetrics(config);

  const stepPct = metrics.steps / Math.max(1, goals.stepGoal);
  const floorPct = metrics.floors / Math.max(1, goals.floorGoal);
  const activePct = metrics.activeMinutes / Math.max(1, goals.activeMinuteGoal);

  const setTheme = (id: string) => {
    void updateWidgetConfig(userSettings.widget_key, { themeId: id });
  };

  const track = cssVars['--ht-progress-track'] ?? theme.tokens.border;
  const glow = theme.tokens.glow;

  return (
    <WidgetCard
      title="Health"
      settingsButton={
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="glass-button px-2 py-0.5 text-[11px]"
            onClick={() => setShowThemes((v) => !v)}
            aria-pressed={showThemes}
          >
            Themes
          </button>
          {settingsButton}
        </div>
      }
      accentColor={theme.tokens.accent}
      expandedClassName="bg-black/80"
    >
      <div
        data-theme-id={theme.id}
        data-testid="health-activity-widget"
        className="h-full flex flex-col gap-3 p-1 rounded-xl relative overflow-hidden"
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

        {showThemes && (
          <div className="rounded-lg p-2" style={{ background: cssVars['--ht-surface'] }}>
            <ThemePackageManager
              compact
              surface="android_widget"
              selectedThemeId={themeId}
              onSelectTheme={setTheme}
            />
          </div>
        )}

        <div className="flex items-center justify-between gap-2">
          <div>
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
              {metrics.steps.toLocaleString()}
              <span className="text-sm font-medium ml-1" style={{ color: cssVars['--ht-text-muted'] }}>
                / {goals.stepGoal.toLocaleString()}
              </span>
            </div>
          </div>
          <Ring
            pct={stepPct}
            stroke={cssVars['--ht-ring'] ?? cssVars['--ht-progress']}
            track={track}
            size={88}
          >
            <Footprints size={22} style={{ color: cssVars['--ht-accent'] }} aria-hidden />
          </Ring>
        </div>

        <div className="grid grid-cols-3 gap-2">
          {[
            {
              key: 'floors',
              label: 'Stairs',
              value: metrics.floors,
              goal: goals.floorGoal,
              pct: floorPct,
              icon: Mountain,
              ring: cssVars['--ht-ring-2'] ?? cssVars['--ht-accent'],
            },
            {
              key: 'active',
              label: 'Active',
              value: metrics.activeMinutes,
              goal: goals.activeMinuteGoal,
              pct: activePct,
              icon: Timer,
              ring: cssVars['--ht-ring-3'] ?? cssVars['--ht-progress'],
              unit: 'm',
            },
            {
              key: 'kcal',
              label: 'Kcal',
              value: metrics.calories,
              goal: 500,
              pct: metrics.calories / 500,
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
                  {tile.value}
                  {tile.unit ?? ''}
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
          className="text-[11px] text-center"
          style={{ color: cssVars['--ht-text-muted'] }}
        >
          Theme: <strong style={{ color: cssVars['--ht-accent'] }}>{theme.name}</strong>
          {' · '}
          <button
            type="button"
            className="underline"
            onClick={() => setShowThemes((v) => !v)}
          >
            change
          </button>
        </div>
      </div>
    </WidgetCard>
  );
};

export default HealthActivityWidget;
