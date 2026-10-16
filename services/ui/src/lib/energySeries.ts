import type { TelemetrySummary } from '../types/api';

export interface EnergyDataPoint {
  time: string;
  power: number;
}

export type EnergySummary = NonNullable<TelemetrySummary['summary']>;

/** Chart resolution. The ingestion loop samples every minute and a summary
 * carries the last 100 samples, so 5-minute buckets give ~20 points. */
const BUCKET_S = 300;

/**
 * Household draw over time: each device's average within a bucket, summed
 * across devices. Buckets are keyed by absolute time, not clock hour, so
 * samples from different days never land in the same point.
 */
export function householdSeries(summaries: Record<string, EnergySummary>): EnergyDataPoint[] {
  const buckets = new Map<number, Map<string, number[]>>();
  for (const [entityId, summary] of Object.entries(summaries)) {
    for (const dp of summary.data_points ?? []) {
      if (dp.power_w == null) continue;
      const bucket = Math.floor(dp.recorded_at / BUCKET_S) * BUCKET_S;
      const perEntity = buckets.get(bucket) ?? new Map<string, number[]>();
      const values = perEntity.get(entityId) ?? [];
      values.push(dp.power_w);
      perEntity.set(entityId, values);
      buckets.set(bucket, perEntity);
    }
  }
  return [...buckets.entries()]
    .sort(([a], [b]) => a - b)
    .map(([bucket, perEntity]) => {
      let total = 0;
      for (const values of perEntity.values()) {
        total += values.reduce((x, y) => x + y, 0) / values.length;
      }
      return {
        time: new Date(bucket * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }),
        power: Math.round(total),
      };
    });
}
