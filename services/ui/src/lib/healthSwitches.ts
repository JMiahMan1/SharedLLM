/**
 * The per-metric on/off switches (Health → Health → Cards) and kid mode.
 *
 * The server resolves every switch (`profile.metrics`), so a card is shown
 * only when the profile says it is on; nothing here keeps its own defaults.
 */
import type { HealthMetricSwitch, HealthProfile } from '../types/api';

export function metricOn(profile: HealthProfile | null | undefined, key: HealthMetricSwitch): boolean {
  return profile?.metrics?.[key] === true;
}

/** Kid mode hides calorie numbers, ranges, density colours, weight and fasting. */
export function isKidMode(profile: HealthProfile | null | undefined): boolean {
  return profile?.kid_mode === true;
}
