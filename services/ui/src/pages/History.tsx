import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import PageHeader from '../components/ui/PageHeader';
import { CardSkeletons, EmptyState, ErrorRetry } from '../components/ui/StateViews';
import StatisticsStatusCard from '../components/energy/StatisticsStatusCard';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, History as HistoryIcon, Search, X } from 'lucide-react';
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  DOMAIN_LABELS, HISTORY_PRESETS, MAX_HISTORY_ENTITIES, failureText, getEntities, getHistory, getStatistics, isNumericSeries,
  mergeNumericSeries, numericPoints, parseEntityParam, rangeFromParams, seriesColour, stateColour, timelineSegments,
  type HistorySeries, type PickerEntity, type StatRow,
} from '../lib/dashboards';

const HOUR_MS = 3600 * 1000;
/** Past this, raw history is too dense to draw; hourly statistics are used where a sensor has them. */
export const STATISTICS_AFTER_HOURS = 24 * 7;
const TOOLTIP_STYLE = {
  background: 'rgba(3, 7, 17, 0.95)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', fontSize: '11px',
};

function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

interface Loaded {
  history: HistorySeries;
  stats: Record<string, StatRow[]>;
  startMs: number;
  endMs: number;
}

function EntityPicker({ selected, onChange }: { selected: string[]; onChange: (ids: string[]) => void }) {
  const [q, setQ] = useState('');
  const { data: all } = useQuery({ queryKey: ['dashboard', 'entities'], queryFn: () => getEntities() });
  const byId = useMemo(() => new Map((all ?? []).map((e) => [e.entity_id, e])), [all]);
  const matches = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return [];
    return (all ?? []).filter((e) => !selected.includes(e.entity_id)
      && (e.name.toLowerCase().includes(needle) || e.entity_id.includes(needle))).slice(0, 12);
  }, [all, q, selected]);
  const full = selected.length >= MAX_HISTORY_ENTITIES;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {selected.map((id) => (
          <span key={id} className="inline-flex items-center gap-1 rounded-full bg-white/10 pl-3 pr-1 text-xs" data-testid="history-chip">
            {byId.get(id)?.name ?? id}
            <button type="button" aria-label={`Remove ${byId.get(id)?.name ?? id}`} className="min-h-11 min-w-11 flex items-center justify-center"
              onClick={() => onChange(selected.filter((x) => x !== id))}><X size={12} /></button>
          </span>
        ))}
      </div>
      <label className="relative block">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" aria-hidden />
        <input value={q} onChange={(e) => setQ(e.target.value)} disabled={full}
          placeholder={full ? `Up to ${MAX_HISTORY_ENTITIES} at once` : 'Add a device or sensor...'}
          aria-label="Search entities" className="w-full min-h-11 rounded-xl bg-white/5 border border-white/10 pl-9 pr-3 text-sm" />
      </label>
      {matches.length > 0 && (
        <ul className="rounded-xl border border-white/10 divide-y divide-white/5" role="listbox" aria-label="Matching entities">
          {matches.map((e: PickerEntity) => (
            <li key={e.entity_id}>
              <button type="button" role="option" aria-selected={false} className="w-full min-h-11 px-3 text-left text-sm flex justify-between gap-2"
                onClick={() => { onChange([...selected, e.entity_id]); setQ(''); }}>
                <span className="truncate">{e.name}</span>
                <span className="text-xs text-slate-500 shrink-0">{DOMAIN_LABELS[e.domain] ?? e.domain}{e.unit ? ` · ${e.unit}` : ''}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Timeline({ name, points, startMs, endMs }: { name: string; points: HistorySeries[string]; startMs: number; endMs: number }) {
  const segs = timelineSegments(points, startMs, endMs);
  const span = endMs - startMs || 1;
  return (
    <div className="space-y-1" data-testid="history-timeline">
      <p className="text-xs text-slate-300">{name}</p>
      <div className="relative h-7 rounded-md overflow-hidden bg-white/5">
        {segs.map((s) => (
          <div key={`${s.from}`} title={`${s.state}: ${new Date(s.from).toLocaleString()} - ${new Date(s.to).toLocaleString()}`}
            className="absolute top-0 h-full text-[10px] text-white/90 overflow-hidden whitespace-nowrap px-1 leading-7"
            style={{ left: `${((s.from - startMs) / span) * 100}%`, width: `${((s.to - s.from) / span) * 100}%`, background: stateColour(s.state) }}>
            {s.state.replace(/_/g, ' ')}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function History() {
  const isAdmin = Boolean(useAuth().user?.is_admin);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [params, setParams] = useSearchParams();
  const selected = parseEntityParam(params.get('e'));
  const range = rangeFromParams(params);
  const { data: all } = useQuery({ queryKey: ['dashboard', 'entities'], queryFn: () => getEntities() });
  const meta = useMemo(() => new Map((all ?? []).map((e) => [e.entity_id, e])), [all]);
  const useStats = (range.end.getTime() - range.start.getTime()) / HOUR_MS > STATISTICS_AFTER_HOURS;

  const update = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v == null || v === '') next.delete(k);
      else next.set(k, v);
    }
    setParams(next, { replace: true });
  };

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['dashboard', 'history', selected.join(','), range.key, range.key === 'custom' ? range.start.toISOString() : '',
      range.key === 'custom' ? range.end.toISOString() : ''],
    enabled: selected.length > 0,
    refetchInterval: range.key === 'custom' ? false : 60_000,
    queryFn: async (): Promise<Loaded> => {
      let stats: Record<string, StatRow[]> = {};
      if (useStats) {
        const numeric = selected.filter((id) => meta.get(id)?.unit || id.startsWith('sensor.'));
        if (numeric.length) {
          try {
            stats = await getStatistics(numeric, range.start, range.end, 'hour', ['mean', 'min', 'max']);
          } catch {
            stats = {};  // fall back to raw history below
          }
        }
      }
      const rawIds = selected.filter((id) => !(stats[id]?.length));
      const history = rawIds.length ? await getHistory(rawIds, range.start, range.end) : {};
      return { history, stats, startMs: range.start.getTime(), endMs: range.end.getTime() };
    },
  });

  const charts = (() => {
    if (!data) return { byUnit: [] as { unit: string; ids: string[]; rows: ReturnType<typeof mergeNumericSeries> }[], timelines: [] as string[] };
    const byUnit = new Map<string, Record<string, { t: number; v: number | null }[]>>();
    const timelines: string[] = [];
    for (const id of selected) {
      const unit = meta.get(id)?.unit ?? '';
      let pts: { t: number; v: number | null }[] | null = null;
      if (data.stats[id]?.length) pts = data.stats[id].map((r) => ({ t: r.start, v: r.mean ?? r.state ?? null }));
      else if (data.history[id] && isNumericSeries(data.history[id])) pts = numericPoints(data.history[id]);
      if (pts) {
        const group = byUnit.get(unit) ?? {};
        group[id] = pts;
        byUnit.set(unit, group);
      } else if (data.history[id]?.length) {
        timelines.push(id);
      }
    }
    return {
      byUnit: [...byUnit.entries()].map(([unit, series]) => ({ unit, ids: Object.keys(series), rows: mergeNumericSeries(series, data.endMs) })),
      timelines,
    };
  })();

  const nameOf = (id: string) => meta.get(id)?.name ?? id;
  const spanHours = (range.end.getTime() - range.start.getTime()) / HOUR_MS;
  const tick = (t: number) => spanHours <= 36
    ? new Date(t).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    : new Date(t).toLocaleDateString([], { month: 'short', day: 'numeric' });

  return (
    <div className="mx-auto w-full max-w-7xl space-y-4 pb-24" data-testid="history-page">
      <PageHeader icon={HistoryIcon} tone="text-sky-400" title="History"
        subtitle="How any device or sensor changed over time." />
      <div className="glass-card rounded-2xl p-4 space-y-3">
        <EntityPicker selected={selected} onChange={(ids) => update({ e: ids.join(',') || null })} />
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Range">
          {HISTORY_PRESETS.map((p) => (
            <button key={p.key} type="button" aria-pressed={range.key === p.key}
              className={`min-h-11 rounded-xl px-3 text-sm ${range.key === p.key ? 'bg-sky-500/20 text-sky-200' : 'bg-white/5'}`}
              onClick={() => update({ r: p.key, from: null, to: null })}>{p.label}</button>
          ))}
          <label className="text-xs text-slate-400 flex items-center gap-1">From
            <input type="datetime-local" className="min-h-11 rounded-xl bg-white/5 px-2 text-sm" value={toLocalInput(range.start)}
              onChange={(e) => update({ from: new Date(e.target.value).toISOString(), to: range.end.toISOString(), r: null })} />
          </label>
          <label className="text-xs text-slate-400 flex items-center gap-1">To
            <input type="datetime-local" className="min-h-11 rounded-xl bg-white/5 px-2 text-sm" value={toLocalInput(range.end)}
              onChange={(e) => update({ to: new Date(e.target.value).toISOString(), from: range.start.toISOString(), r: null })} />
          </label>
        </div>
      </div>

      {selected.length === 0 && (
        <EmptyState icon={<HistoryIcon size={28} />} title="Pick something to chart"
          detail="Search above for a device or sensor, a thermostat or a power circuit, and add up to twelve at once." />
      )}
      {isLoading && selected.length > 0 && <CardSkeletons count={1} className="grid" height="h-64" label="Loading history" />}
      {error && <ErrorRetry title={failureText(error, 'Could not load the history')} onRetry={() => void refetch()} />}

      {charts.byUnit.map(({ unit, ids, rows }) => (
        <section key={unit || 'none'} className="glass-card rounded-2xl p-4" aria-label={unit ? `Values in ${unit}` : 'Values'}>
          {unit && <h2 className="text-xs font-black uppercase tracking-widest text-slate-500 mb-2">{unit}</h2>}
          <div className="h-64" data-testid="history-chart">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.15)" />
                <XAxis dataKey="t" type="number" scale="time" domain={[data?.startMs ?? 'dataMin', data?.endMs ?? 'dataMax']}
                  tickFormatter={tick} tick={{ fontSize: 10, fill: '#64748b' }} />
                <YAxis tick={{ fontSize: 10, fill: '#64748b' }} width={44} domain={['auto', 'auto']} />
                <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={(t) => new Date(Number(t)).toLocaleString()}
                  formatter={(v, n) => [typeof v === 'number' ? `${Math.round(v * 100) / 100}${unit ? ` ${unit}` : ''}` : '--', n]} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                {ids.map((id, i) => (
                  <Line key={id} type="stepAfter" dataKey={id} name={nameOf(id)} stroke={seriesColour(i)} dot={false}
                    strokeWidth={1.75} isAnimationActive={false} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </section>
      ))}

      {charts.timelines.length > 0 && data && (
        <section className="glass-card rounded-2xl p-4 space-y-3" aria-label="States">
          {charts.timelines.map((id) => (
            <Timeline key={id} name={nameOf(id)} points={data.history[id]} startMs={data.startMs} endMs={data.endMs} />
          ))}
        </section>
      )}
      {isAdmin && (
        <details className="glass-panel rounded-2xl p-4 group" onToggle={(e) => setAdvancedOpen(e.currentTarget.open)}>
          <summary className="min-h-11 flex cursor-pointer list-none items-center gap-2 text-sm font-medium text-white">
            <ChevronDown size={16} className="transition-transform group-open:rotate-180" aria-hidden />
            Advanced: long-term history
          </summary>
          {/* Read from the hub only once opened. */}
          {advancedOpen && <div className="pt-3"><StatisticsStatusCard /></div>}
        </details>
      )}
    </div>
  );
}
