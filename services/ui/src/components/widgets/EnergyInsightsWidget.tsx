import React, { useMemo } from 'react';
import { Zap, TrendingUp, Calendar, Clock } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import type { IWidgetProps } from '../../types/widget';
import { householdSeries, type EnergySummary as Summary } from '../../lib/energySeries';
import { api } from '../../services/api';
import { WidgetCard } from './WidgetCard';
import { useAuth } from '../../context/AuthContext';

const EnergyInsightsWidget = ({ settingsButton }: IWidgetProps) => {
  // Energy readings are the admin's (the gateway refuses anyone else with 403);
  // asking anyway put a failed request in the logs on every dashboard load.
  const isAdmin = useAuth().role === 'admin';
  const [summaries, setSummaries] = React.useState<Record<string, Summary>>({});
  const [loading, setLoading] = React.useState(isAdmin);
  const [error, setError] = React.useState<string | null>(isAdmin ? null : 'Energy insights are available to the household admin.');

  React.useEffect(() => {
    if (!isAdmin) return undefined;
    const fetchEnergyData = async () => {
      try {
        setError(null);
        const enrolled = await api.getTelemetryEnrollments();
        const powerEnrollments = enrolled.filter((e) => e.power_tracking);

        // Fetch all entity summaries in parallel; failed ones are skipped
        const results = await Promise.allSettled(
          powerEnrollments.map((enrollment) =>
            api.getTelemetrySummary(enrollment.entity_id)
          )
        );

        const summaryMap: Record<string, Summary> = {};
        results.forEach((result, idx) => {
          if (result.status === 'fulfilled' && result.value.summary) {
            summaryMap[powerEnrollments[idx].entity_id] = result.value.summary;
          }
        });

        setSummaries(summaryMap);
      } catch (err) {
        const msg = (err as Error).message?.toLowerCase() ?? '';
        if (msg.includes('not found') || msg.includes('404') || msg.includes('connect') || msg.includes('refused')) {
          setError('Energy data is unavailable right now. Try again in a minute.');
        } else {
          setError((err as Error).message || 'Failed to load energy data');
        }
      } finally {
        setLoading(false);
      }
    };
    fetchEnergyData();
    const interval = setInterval(fetchEnergyData, 60000);
    return () => clearInterval(interval);
  }, [isAdmin]);

  const data = useMemo(() => householdSeries(summaries), [summaries]);

  const metrics = useMemo(() => {
    let currentPower = 0;
    let peakPower = 0;
    let peakAt: number | null = null;
    let peakDuration = 0;
    let totalPower = 0;

    for (const summary of Object.values(summaries)) {
      if (summary.current_power_w != null) currentPower += summary.current_power_w;
      if (summary.peak_power_w != null) {
        peakPower = Math.max(peakPower, summary.peak_power_w);
        if (summary.peak_at != null && (peakAt == null || summary.peak_at > peakAt)) {
          peakAt = summary.peak_at;
          peakDuration = summary.peak_duration_seconds ?? 0;
        }
      }
      // Current is the household total, so the average must be one too: the
      // sum of each device's average, not the mean of them.
      if (summary.avg_power_w != null) totalPower += summary.avg_power_w;
    }

    return {
      current: Math.round(currentPower),
      avg: Math.round(totalPower),
      peak: Math.round(peakPower),
      peakAt,
      peakDuration,
    };
  }, [summaries]);

  const formatPeakTime = (timestamp: number | null) => {
    if (!timestamp) return '--';
    // A full date-time wraps to four lines in a third of a phone-width card.
    return new Date(timestamp * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  };

  const formatPeakDuration = (seconds: number | null) => {
    if (!seconds || seconds === 0) return '--';
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    if (hrs > 0) return `${hrs}h ${mins}m`;
    return `${mins}m`;
  };

  const hasData = data.length > 0;

  // Per-device breakdown: each enrolled entity's current draw and its share of total usage
  const deviceBreakdown = useMemo(() => {
    const total = Object.values(summaries).reduce((s, sum) => s + (sum.current_power_w || 0), 0);
    return Object.entries(summaries)
      .map(([entityId, sum]) => {
        // Reported watts arrive as raw floats from the meters, and a row that
        // reads "2.8517875717626W" is unreadable next to the whole-watt cards
        // above it. Only the displayed number is rounded; the share is worked
        // out from the real figure so a rounded numerator cannot push the
        // percentages past a hundred.
        const raw = sum.current_power_w || 0;
        const current = Math.round(raw);
        return {
          entityId,
          name: entityId.split('.').slice(1).join('.') || entityId,
          current,
          pct: total > 0 ? Math.round((raw / total) * 100) : 0,
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
              <p className="text-[9px] font-black uppercase tracking-widest text-slate-500" title="Highest single-device reading">Peak</p>
              <p className="text-base font-bold text-red-400">{metrics.peak}<span className="text-[10px] ml-0.5 text-slate-500">W</span></p>
              {metrics.peakAt && (
                <div className="flex items-center justify-center gap-1 mt-1">
                  <Calendar size={10} className="text-red-400" />
                  <span className="text-[9px] text-slate-400">{formatPeakTime(metrics.peakAt)}</span>
                </div>
              )}
              {metrics.peakDuration > 0 && (
                <div className="flex items-center justify-center gap-1 mt-0.5">
                  <Clock size={10} className="text-slate-400" />
                  <span className="text-[9px] text-slate-500">Duration: {formatPeakDuration(metrics.peakDuration)}</span>
                </div>
              )}
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
          <p className="text-sm text-slate-500">No energy data available</p>
          <p className="text-xs text-slate-600 mt-1">
            Power-tracked devices appear here once an admin enrolls them under Telemetry.
          </p>
        </div>
      )}
    </WidgetCard>
  );
};

export default EnergyInsightsWidget;
