import React, { useMemo } from 'react';
import { Zap, TrendingUp } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import type { IWidgetProps } from '../../types/widget';
import { api } from '../../services/api';
import { WidgetCard } from './WidgetCard';

interface EnergyDataPoint {
  time: string;
  power: number;
  battery?: number;
}

interface TelemetrySummary {
  current_power_w: number | null;
  peak_power_w: number | null;
  avg_power_w: number | null;
  availability_pct: number;
  total_activations: number;
  data_points: Array<{
    recorded_at: number;
    power_w?: number;
    is_available?: boolean;
    state?: string;
    source?: string;
  }>;
}

const EnergyInsightsWidget = ({ settingsButton }: IWidgetProps) => {
  const [summaries, setSummaries] = React.useState<Record<string, TelemetrySummary>>({});
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const fetchEnergyData = async () => {
      try {
        setError(null);
        const enrolled = await api.getTelemetryEnrollments();
        const powerEnrollments = enrolled.filter((e) => e.power_tracking);

        // Fetch all entity summaries in parallel — failed ones are skipped gracefully
        const results = await Promise.allSettled(
          powerEnrollments.map((enrollment) =>
            api.getTelemetrySummary(enrollment.entity_id)
          )
        );

        const summaryMap: Record<string, TelemetrySummary> = {};
        results.forEach((result, idx) => {
          if (result.status === 'fulfilled' && result.value.summary) {
            summaryMap[powerEnrollments[idx].entity_id] = result.value.summary;
          }
        });

        setSummaries(summaryMap);
      } catch {
        setError('Telemetry Service Unconfigured');
      } finally {
        setLoading(false);
      }
    };
    fetchEnergyData();
  }, []);

  const data = useMemo(() => {
    const allDataPoints: Array<{ recorded_at: number; power_w?: number; entity_id: string }> = [];

    for (const [entityId, summary] of Object.entries(summaries)) {
      if (summary.data_points) {
        for (const dp of summary.data_points) {
          if (dp.power_w != null) {
            allDataPoints.push({ recorded_at: dp.recorded_at, power_w: dp.power_w, entity_id: entityId });
          }
        }
      }
    }

    if (allDataPoints.length === 0) return [];

    allDataPoints.sort((a, b) => a.recorded_at - b.recorded_at);

    const hourlyBuckets: Record<string, number[]> = {};
    for (const dp of allDataPoints) {
      const date = new Date(dp.recorded_at * 1000);
      const key = `${date.getHours().toString().padStart(2, '0')}:00`;
      if (!hourlyBuckets[key]) hourlyBuckets[key] = [];
      hourlyBuckets[key].push(dp.power_w!);
    }

    const result: EnergyDataPoint[] = [];
    for (const [time, values] of Object.entries(hourlyBuckets).sort()) {
      const avg = Math.round(values.reduce((a, b) => a + b, 0) / values.length);
      result.push({ time, power: avg });
    }

    return result;
  }, [summaries]);

  const metrics = useMemo(() => {
    let currentPower = 0;
    let peakPower = 0;
    let totalPower = 0;
    let powerCount = 0;

    for (const summary of Object.values(summaries)) {
      if (summary.current_power_w != null) currentPower += summary.current_power_w;
      if (summary.peak_power_w != null) peakPower = Math.max(peakPower, summary.peak_power_w);
      if (summary.avg_power_w != null) {
        totalPower += summary.avg_power_w;
        powerCount++;
      }
    }

    return {
      current: Math.round(currentPower),
      avg: powerCount > 0 ? Math.round(totalPower / powerCount) : 0,
      peak: Math.round(peakPower),
    };
  }, [summaries]);

  const hasData = data.length > 0;

  // Per-device breakdown: each enrolled entity's current draw and its share of total usage
  const deviceBreakdown = useMemo(() => {
    const total = Object.values(summaries).reduce((s, sum) => s + (sum.current_power_w || 0), 0);
    return Object.entries(summaries)
      .map(([entityId, sum]) => {
        const current = sum.current_power_w || 0;
        return {
          entityId,
          name: entityId.split('.').slice(1).join('.') || entityId,
          current,
          pct: total > 0 ? Math.round((current / total) * 100) : 0,
        };
      })
      .sort((a, b) => b.current - a.current);
  }, [summaries]);

  return (
    <WidgetCard
      title="Energy Insights"
      icon={<Zap size={16} className="text-amber-400" />}
      isLoading={loading}
      error={error}
      settingsButton={settingsButton}
      accentColor="#f59e0b"
    >
      {hasData ? (
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-2">
            <div className="glass-card p-2.5 text-center">
              <p className="text-[9px] font-black uppercase tracking-widest text-slate-500">Current</p>
              <p className="text-base font-bold text-amber-400">{metrics.current}<span className="text-[10px] ml-0.5 text-slate-500">W</span></p>
            </div>
            <div className="glass-card p-2.5 text-center">
              <p className="text-[9px] font-black uppercase tracking-widest text-slate-500">Average</p>
              <p className="text-base font-bold text-blue-400">{metrics.avg}<span className="text-[10px] ml-0.5 text-slate-500">W</span></p>
            </div>
            <div className="glass-card p-2.5 text-center">
              <p className="text-[9px] font-black uppercase tracking-widest text-slate-500">Peak</p>
              <p className="text-base font-bold text-red-400">{metrics.peak}<span className="text-[10px] ml-0.5 text-slate-500">W</span></p>
            </div>
          </div>

          {Object.keys(summaries).length > 0 && (
            <div className="flex items-center justify-between px-3 py-2 rounded-xl bg-black/20 border border-white/5">
              <p className="text-xs text-slate-500">Devices tracked</p>
              <div className="flex items-center gap-1.5">
                <TrendingUp size={12} className="text-emerald-400" />
                <span className="text-xs font-bold text-emerald-400">{Object.keys(summaries).length}</span>
              </div>
            </div>
          )}

          {deviceBreakdown.length > 0 && (
            <div className="space-y-1.5 px-1">
              {deviceBreakdown.map((d) => (
                <div key={d.entityId} className="flex items-center justify-between text-xs">
                  <span className="truncate text-slate-400 max-w-[62%]" title={d.entityId}>{d.name}</span>
                  <span className="text-slate-300 font-medium tabular-nums">
                    {d.current}W <span className="text-slate-600">· {d.pct}%</span>
                  </span>
                </div>
              ))}
            </div>
          )}

          <div className="h-24">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data}>
                <defs>
                  <linearGradient id="powerGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="time" tick={{ fontSize: 9, fill: '#64748b' }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 9, fill: '#64748b' }} axisLine={false} tickLine={false} width={28} />
                <Tooltip
                  contentStyle={{
                    background: 'rgba(3, 7, 17, 0.95)',
                    border: '1px solid rgba(255,255,255,0.1)',
                    borderRadius: '8px',
                    fontSize: '11px',
                  }}
                />
                <Area type="monotone" dataKey="power" stroke="#f59e0b" strokeWidth={2} fill="url(#powerGradient)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center h-full text-center py-4">
          <Zap size={28} className="text-slate-700 mb-2" />
          <p className="text-sm text-slate-500">No energy data</p>
          <p className="text-xs text-slate-600 mt-1">Enroll devices in Admin → Telemetry</p>
        </div>
      )}
    </WidgetCard>
  );
};

export default EnergyInsightsWidget;
