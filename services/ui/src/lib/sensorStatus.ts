/**
 * Presentation model for a tracking sensor's health.
 *
 * Pure functions only, deliberately kept out of the component so the rules are
 * testable and so `SensorStatusBanner` stays presentational. That matters here
 * because the whole point of this module is to make a *silent* stall visible:
 * before it existed, a phone that had stopped reporting just rendered stale or
 * empty data and nobody could tell "no activity" from "tracking is broken".
 *
 * The one judgement call is the precedence order. A sensor can be `recovering`
 * while still `enabled` (a transient failure is being retried), so recovery
 * has to outrank the enabled check or it would never render.
 */

export type SensorHealth = 'ok' | 'recovering' | 'off' | 'denied' | 'unavailable';

export type SensorStatusInput = {
  enabled: boolean;
  permission: 'unknown' | 'granted' | 'denied' | 'unavailable' | 'disabled';
  message: string | null;
  recovering: boolean;
};

export interface SensorStatusView {
  health: SensorHealth;
  /** Short headline: what is wrong, in the user's terms. */
  title: string;
  /** What it means for their data — the part that makes the notice actionable. */
  detail: string;
  /** A one-tap re-enable is meaningful here. */
  canEnable: boolean;
  /** We can only fix this from the phone's OS settings, not from here. */
  needsOsSettings: boolean;
}

type SensorLabel = {
  title: string;
  /** Data consequence while this sensor is not reporting. */
  gap: string;
  /** Data consequence while it is retrying. */
  lagging: string;
};

const LABELS: Record<string, SensorLabel> = {
  location: {
    title: 'Location sharing',
    gap: 'Your location is missing from the family map and trip tracking.',
    lagging: 'Reconnecting automatically — a short drive may be missing from your trips.',
  },
  steps: {
    title: 'Step tracking',
    gap: 'Your daily steps and streaks will have gaps.',
    lagging: 'Reconnecting automatically — today’s total may lag a little.',
  },
};

function labelFor(sensor: string): SensorLabel {
  return (
    LABELS[sensor] ?? {
      title: 'Tracking',
      gap: 'This sensor is not reporting.',
      lagging: 'Reconnecting automatically.',
    }
  );
}

/**
 * Classify a sensor for display.
 *
 * Precedence, highest first — the order is the whole design:
 *   1. `unavailable` — the hardware or API does not exist; nothing to enable.
 *   2. `denied` — the OS refused; only the OS settings can undo it.
 *   3. `recovering` — a transient failure is being retried on its own.
 *   4. `off` — the user (or a previous denial) turned it off; re-enable works.
 *   5. `ok` — healthy, so render nothing.
 */
export function sensorStatus(sensor: string, state: SensorStatusInput): SensorStatusView {
  const label = labelFor(sensor);

  if (state.permission === 'unavailable') {
    return {
      health: 'unavailable',
      title: `${label.title} is not available on this device`,
      detail: state.message ?? 'This phone does not expose that sensor, so nothing is being recorded.',
      canEnable: false,
      needsOsSettings: false,
    };
  }

  if (state.permission === 'denied') {
    return {
      health: 'denied',
      title: `${label.title} permission was denied`,
      detail: state.message ?? label.gap,
      canEnable: false,
      needsOsSettings: true,
    };
  }

  if (state.recovering) {
    return {
      health: 'recovering',
      title: `${label.title} is reconnecting`,
      detail: state.message ?? label.lagging,
      canEnable: false,
      needsOsSettings: false,
    };
  }

  if (!state.enabled) {
    return {
      health: 'off',
      title: `${label.title} is off`,
      detail: state.message ?? label.gap,
      canEnable: true,
      needsOsSettings: false,
    };
  }

  return {
    health: 'ok',
    title: '',
    detail: state.message ?? '',
    canEnable: false,
    needsOsSettings: false,
  };
}

/** True when nothing is wrong and the banner should render nothing at all. */
export function isHealthy(view: SensorStatusView): boolean {
  return view.health === 'ok';
}

/**
 * The single most urgent sensor problem, for a collapsed summary line.
 *
 * Severity order matches `sensorStatus`'s precedence but is deliberately
 * independent of it: a denial is worth more of the user's attention than a
 * sensor that is merely switched off.
 */
const SEVERITY: Record<SensorHealth, number> = {
  denied: 4,
  unavailable: 3,
  recovering: 2,
  off: 1,
  ok: 0,
};

export function worstOf(views: SensorStatusView[]): SensorStatusView | null {
  let worst: SensorStatusView | null = null;
  for (const view of views) {
    if (view.health === 'ok') continue;
    if (!worst || SEVERITY[view.health] > SEVERITY[worst.health]) worst = view;
  }
  return worst;
}