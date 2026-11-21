import type { QueryClient } from '@tanstack/react-query';

/**
 * Every query a garden change can make stale.
 *
 * One watering moves the plant's next date, the day's due list, the widget's
 * count, the streak, and (on a harvest) the recipe matches. Each mutation used
 * to invalidate two or three of these by hand, so a screen would keep showing
 * yesterday's answer until it was reloaded.
 */
export const GARDEN_QUERY_KEYS = [
  'garden-today',
  'garden-today-widget',
  'garden-plants',
  'garden-streaks',
  'garden-harvest-ready',
  'garden-harvest-forecast',
  'garden-recipe-matches',
  'garden-pantry',
  'garden-bindings',
  'garden-hardware',
  'garden-tag',
  'shopping-list',
] as const;

/** Invalidate the household-wide keys, plus the per-plant ones for `plantId`. */
export function invalidateGarden(queryClient: QueryClient, options?: { plantId?: string }): void {
  for (const key of GARDEN_QUERY_KEYS) {
    void queryClient.invalidateQueries({ queryKey: [key] });
  }
  if (options?.plantId) {
    void queryClient.invalidateQueries({ queryKey: ['garden-plant', options.plantId] });
    void queryClient.invalidateQueries({ queryKey: ['garden-plant-logs', options.plantId] });
    void queryClient.invalidateQueries({ queryKey: ['garden-harvests', options.plantId] });
    void queryClient.invalidateQueries({ queryKey: ['garden-photos', options.plantId] });
  }
}
