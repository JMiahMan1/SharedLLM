import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, History as HistoryIcon, Loader2, Settings2, Thermometer } from 'lucide-react';
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import toast from 'react-hot-toast';
import { useAuth } from '../context/AuthContext';
import { ClimateControl, SensorChip } from '../components/dashboards/EntityControls';
import {
  failureText, formatPower, getClimate, getDashboardPrefs, getEntities, getHistory, mergeNumericSeries,
  numericPoints, saveDashboardPrefs, seriesColour, toWatts, type HubEntity, type Thermostat,
} from '../lib/dashboards';

export const CLIMATE_POLL_MS = 10_000;
const DAY_MS = 24 * 3600 * 1000;
const TOOLTIP_STYLE = {
  background: 'rgba(3, 7, 17, 0.95)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', fontSize: '11px',
};

function ThermostatChart({ thermostat }: { thermostat: Thermostat }) {
  const powerIds = thermostat.power.map((p) => p.entity_id);
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard', 'climate-history', thermostat.entity_id, powerIds.join(',')],
    queryFn: async () => {
      const end = new Date();
      return { end: end.getTime(), series: await getHistory([thermostat.entity_id, ...powerIds], new Date(end.getTime() - DAY_MS), end, true) };
    },
    refetchInterval: 5 * 60_000,
  });
  const { rows, lines } = useMemo(() => {
    if (!data) return { rows: [], lines: [] as { key: string; name: string; axis: string; colour: string }[] };
    const own = data.series[thermostat.entity_id] ?? [];
    const series: Record<string, { t: number; v: number | null }[]> = {
      current: numericPoints(own, 'current_temperature'),
      target: numericPoints(own, 'temperature'),
    };
    const lines = [
      { key: 'current', name: 'Temperature', axis: 'temp', colour: '#f59e0b' },
      { key: 'target', name: 'Target', axis: 'temp', colour: '#94a3b8' },
    ];
    thermostat.power.forEach((p: HubEntity, i) => {
      series[p.entity_id] = numericPoints(data.series[p.entity_id] ?? []).map((pt) => ({ ...pt, v: toWatts(pt.v, p.unit) }));
      lines.push({ key: p.entity_id, name: p.name, axis: 'power', colour: seriesColour(i) });
    });
    return { rows: mergeNumericSeries(series, data.end), lines };
  }, [data, thermostat]);

  if (isLoading) return <p className="text-xs text-slate-500">Loading the last 24 hours...</p>;
  if (!rows.length) return <p className="text-xs text-slate-500">No history for the last 24 hours.</p>;
  return (
    <div className="h-56" data-testid="climate-chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.15)" />
          <XAxis dataKey="t" type="number" domain={['dataMin', 'dataMax']} scale="time"
            tickFormatter={(t: number) => new Date(t).toLocaleTimeString([], { hour: 'numeric' })}
            tick={{ fontSize: 10, fill: '#64748b' }} />
          <YAxis yAxisId="temp" tick={{ fontSize: 10, fill: '#64748b' }} width={32} domain={['auto', 'auto']} unit="°" />
          {thermostat.power.length > 0 && (
            <YAxis yAxisId="power" orientation="right" tick={{ fontSize: 10, fill: '#64748b' }} width={44}
              tickFormatter={(w: number) => formatPower(w)} />
          )}
          <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={(t) => new Date(Number(t)).toLocaleString()}
            formatter={(v, name) => [typeof v === 'number'
              ? (lines.find((l) => l.name === name)?.axis === 'power' ? formatPower(v) : `${Math.round(v * 10) / 10}°`) : '--', name]} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {lines.map((l) => (
            <Line key={l.key} yAxisId={l.axis} type="stepAfter" dataKey={l.key} name={l.name} stroke={l.colour}
              dot={false} strokeWidth={l.axis === 'temp' ? 2 : 1.5} strokeDasharray={l.key === 'target' ? '4 3' : undefined}
              connectNulls={false} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function PowerEditor({ thermostat, onSaved }: { thermostat: Thermostat; onSaved: () => void }) {
  const { data: candidates } = useQuery({
    queryKey: ['dashboard', 'power-sensors'],
    queryFn: async () => (await getEntities(['sensor'])).filter((e) => e.device_class === 'power'),
  });
  const [chosen, setChosen] = useState<string[]>(thermostat.power.map((p) => p.entity_id));
  const [saving, setSaving] = useState(false);
  const save = async (value: string[] | null) => {
    setSaving(true);
    try {
      const prefs = await getDashboardPrefs();
      const next = { ...prefs.climate_power };
      if (value === null) delete next[thermostat.entity_id];
      else next[thermostat.entity_id] = value;
      await saveDashboardPrefs({ climate_power: next });
      toast.success('Saved');
      onSaved();
    } catch (err) {
      toast.error(failureText(err, 'Could not save'));
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="rounded-xl border border-white/10 p-3 space-y-2" data-testid="power-editor">
      <p className="text-xs text-slate-400">
        Power sensors shown with this thermostat. By default these are the power sensors in its room.
      </p>
      <div className="max-h-48 overflow-y-auto space-y-1">
        {(candidates ?? []).map((c) => (
          <label key={c.entity_id} className="flex items-center gap-2 text-sm min-h-9">
            <input type="checkbox" checked={chosen.includes(c.entity_id)}
              onChange={() => setChosen((cur) => cur.includes(c.entity_id) ? cur.filter((x) => x !== c.entity_id) : [...cur, c.entity_id])} />
            <span className="truncate">{c.name}</span>
          </label>
        ))}
      </div>
      <div className="flex gap-2">
        <button type="button" disabled={saving} onClick={() => void save(chosen)}
          className="min-h-11 rounded-xl bg-amber-500/20 px-3 text-sm text-amber-200">Save</button>
        <button type="button" disabled={saving} onClick={() => void save(null)}
          className="min-h-11 rounded-xl bg-white/5 px-3 text-sm">Use the room's sensors</button>
      </div>
    </div>
  );
}

function ThermostatCard({ thermostat, isAdmin, onChanged }: { thermostat: Thermostat; isAdmin: boolean; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  return (
    <article className="glass-card rounded-2xl p-4 space-y-3" data-testid="thermostat-card" aria-label={thermostat.name}>
      <header className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">{thermostat.name}</h2>
          {thermostat.area_name && <p className="text-xs text-slate-500">{thermostat.area_name}</p>}
        </div>
        {isAdmin && (
          <button type="button" aria-label={`Choose power sensors for ${thermostat.name}`} onClick={() => setEditing((e) => !e)}
            className="min-h-11 min-w-11 flex items-center justify-center rounded-xl bg-white/5"><Settings2 size={16} /></button>
        )}
      </header>
      <ClimateControl entity={thermostat} onChanged={onChanged} compact />
      {(thermostat.sensors.length > 0 || thermostat.power.length > 0) && (
        <div className="flex flex-wrap gap-1.5">
          {thermostat.sensors.map((s) => <SensorChip key={s.entity_id} entity={s} />)}
          {thermostat.power.map((p) => (
            <span key={p.entity_id} className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 border border-amber-500/20 px-2.5 py-1 text-xs">
              <span className="text-slate-400">{p.name}</span>
              <span className="text-amber-200 tabular-nums">{formatPower(toWatts(Number.isFinite(Number(p.state)) ? Number(p.state) : null, p.unit))}</span>
            </span>
          ))}
        </div>
      )}
      {editing && <PowerEditor thermostat={thermostat} onSaved={() => { setEditing(false); onChanged(); }} />}
      <ThermostatChart thermostat={thermostat} />
    </article>
  );
}

export default function Climate() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ['dashboard', 'climate'], queryFn: getClimate, refetchInterval: CLIMATE_POLL_MS });
  const onChanged = () => window.setTimeout(() => void queryClient.invalidateQueries({ queryKey: ['dashboard', 'climate'] }), 1500);
  const ids = (data?.thermostats ?? []).flatMap((t) => [t.entity_id, ...t.power.map((p) => p.entity_id)]);
  return (
    <div className="p-4 md:p-6 space-y-4 max-w-7xl mx-auto">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold flex items-center gap-2"><Thermometer size={22} className="text-cyan-400" />Climate</h1>
        <nav className="flex gap-2 text-sm" aria-label="Related pages">
          <Link to="/schedules" className="min-h-11 flex items-center gap-1 rounded-xl bg-white/5 px-3"><CalendarClock size={14} />Schedules</Link>
          {ids.length > 0 && (
            <Link to={`/history?e=${ids.slice(0, 12).join(',')}&r=24h`} className="min-h-11 flex items-center gap-1 rounded-xl bg-white/5 px-3">
              <HistoryIcon size={14} />History
            </Link>
          )}
        </nav>
      </header>
      {isLoading && <p className="flex items-center gap-2 text-slate-400"><Loader2 className="animate-spin" size={16} />Loading thermostats...</p>}
      {error && <p role="alert" className="text-red-400">{failureText(error, 'Could not load the thermostats')}</p>}
      {data && data.thermostats.length === 0 && <p className="text-slate-400">No thermostats you can see.</p>}
      <div className="grid gap-4 lg:grid-cols-2">
        {data?.thermostats.map((t) => (
          <ThermostatCard key={t.entity_id} thermostat={t} isAdmin={Boolean(user?.is_admin)} onChanged={onChanged} />
        ))}
      </div>
    </div>
  );
}
