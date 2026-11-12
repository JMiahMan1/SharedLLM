import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight, Loader2, Settings2, Zap } from 'lucide-react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { useAuth } from '../context/AuthContext';
import EnergySettings from '../components/energy/EnergySettings';
import {
  COST_SOURCE_LABEL, RANGES, bucketLabel, failureText, formatKwh, formatMoney, formatPower, getEnergySummary,
  isCurrentRange, rangeTitle, shiftDate, touSplits, type EnergyRange, type EnergySummary,
} from '../lib/dashboards';

export const ENERGY_POLL_MS = 60_000;
const TOOLTIP_STYLE = {
  background: 'rgba(3, 7, 17, 0.95)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', fontSize: '11px',
};
const SPLIT_COLOURS = ['#f97316', '#38bdf8', '#a78bfa', '#34d399'];

function Kpi({ label, value, hint, tone = 'text-slate-100' }: { label: string; value: string; hint?: string; tone?: string }) {
  return (
    <div className="glass-card rounded-2xl p-3" data-testid="energy-kpi">
      <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">{label}</p>
      <p className={`text-xl font-bold tabular-nums ${tone}`}>{value}</p>
      {hint && <p className="text-[11px] text-slate-500">{hint}</p>}
    </div>
  );
}

function UsageChart({ summary }: { summary: EnergySummary }) {
  const rows = summary.grid.series.map((p) => ({ label: bucketLabel(p.start, summary.period), kwh: Math.round(p.value * 100) / 100 }));
  if (!rows.length) return <p className="text-sm text-slate-500">No usage recorded for this range.</p>;
  return (
    <div className="h-60" data-testid="energy-chart">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.15)" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#64748b' }} interval="preserveStartEnd" />
          <YAxis tick={{ fontSize: 10, fill: '#64748b' }} width={36} unit="" />
          <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => [formatKwh(Number(v)), 'Used']} />
          <Bar dataKey="kwh" fill="#f59e0b" radius={[4, 4, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function DeviceList({ summary }: { summary: EnergySummary }) {
  const max = Math.max(...summary.devices.map((d) => d.kwh), summary.untracked_kwh ?? 0, 0.0001);
  if (!summary.devices.length) return <p className="text-sm text-slate-500">No individual devices are set up.</p>;
  return (
    <ul className="space-y-2">
      {summary.devices.map((d) => (
        <li key={d.stat} data-testid="energy-device">
          <div className="flex justify-between gap-2 text-sm">
            <span className="truncate text-slate-200">{d.name}</span>
            <span className="tabular-nums text-slate-300 shrink-0">
              {formatKwh(d.kwh)}{d.power_w != null && <span className="text-slate-500"> · {formatPower(d.power_w)} now</span>}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-white/5 mt-1">
            <div className="h-full rounded-full bg-amber-500/70" style={{ width: `${Math.max(0, (d.kwh / max) * 100)}%` }} />
          </div>
        </li>
      ))}
      {summary.untracked_kwh != null && summary.untracked_kwh > 0 && (
        <li className="text-sm flex justify-between text-slate-400">
          <span>Everything else</span><span className="tabular-nums">{formatKwh(summary.untracked_kwh)}</span>
        </li>
      )}
    </ul>
  );
}

function TouCard({ summary, isAdmin, openSettings }: { summary: EnergySummary; isAdmin: boolean; openSettings: () => void }) {
  const splits = touSplits(summary);
  if (!splits.length) {
    return (
      <p className="text-sm text-slate-500">
        No time-of-use split yet: set your rate periods in the energy settings
        {isAdmin ? <> (<button type="button" className="underline" onClick={openSettings}>open settings</button>)</> : ' (an admin can)'}.
      </p>
    );
  }
  return (
    <div className="space-y-4">
      {splits.map((split) => {
        const total = split.periods.reduce((s, p) => s + p.kwh, 0) || 1;
        return (
          <div key={split.label} data-testid="tou-split">
            <p className="text-xs text-slate-400 mb-1">{split.label}</p>
            <div className="flex h-3 rounded-full overflow-hidden bg-white/5">
              {split.periods.map((p, i) => (
                <div key={p.name} style={{ width: `${(p.kwh / total) * 100}%`, background: SPLIT_COLOURS[i % SPLIT_COLOURS.length] }}
                  title={`${p.name}: ${formatKwh(p.kwh)}`} />
              ))}
            </div>
            <ul className="mt-2 grid grid-cols-2 gap-1 text-sm">
              {split.periods.map((p, i) => (
                <li key={p.name} className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: SPLIT_COLOURS[i % SPLIT_COLOURS.length] }} aria-hidden />
                  <span className="text-slate-300">{p.name}</span>
                  <span className="tabular-nums text-slate-400">{formatKwh(p.kwh)} ({Math.round((p.kwh / total) * 100)}%)</span>
                  {p.cost != null && <span className="text-slate-500">{formatMoney(p.cost, summary.currency)}</span>}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

export default function Energy() {
  const { user } = useAuth();
  const isAdmin = Boolean(user?.is_admin);
  const queryClient = useQueryClient();
  const [range, setRange] = useState<EnergyRange>('day');
  const [date, setDate] = useState(() => new Date());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const current = isCurrentRange(range, date);
  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: ['dashboard', 'energy', range, date.toDateString()],
    queryFn: () => getEnergySummary(range, date),
    refetchInterval: current ? ENERGY_POLL_MS : false,
  });

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-7xl mx-auto">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold flex items-center gap-2"><Zap size={22} className="text-amber-400" />Energy</h1>
        {isAdmin && (
          <button type="button" className="min-h-11 flex items-center gap-1 rounded-xl bg-white/5 px-3 text-sm"
            onClick={() => setSettingsOpen((o) => !o)} aria-expanded={settingsOpen}>
            <Settings2 size={14} />Settings
          </button>
        )}
      </header>

      {settingsOpen && isAdmin && (
        <EnergySettings onClose={() => setSettingsOpen(false)}
          onSaved={() => void queryClient.invalidateQueries({ queryKey: ['dashboard', 'energy'] })} />
      )}

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-xl bg-white/5 p-1" role="tablist" aria-label="Range">
          {RANGES.map((r) => (
            <button key={r.key} type="button" role="tab" aria-selected={range === r.key}
              className={`min-h-9 rounded-lg px-3 text-sm ${range === r.key ? 'bg-amber-500/25 text-amber-100' : ''}`}
              onClick={() => setRange(r.key)}>{r.label}</button>
          ))}
        </div>
        <button type="button" aria-label="Previous" className="min-h-11 min-w-11 flex items-center justify-center rounded-xl bg-white/5"
          onClick={() => setDate((d) => shiftDate(range, d, -1))}><ChevronLeft size={16} /></button>
        <span className="text-sm text-slate-300 min-w-40 text-center" data-testid="energy-range-title">{rangeTitle(range, date)}</span>
        <button type="button" aria-label="Next" disabled={current}
          className="min-h-11 min-w-11 flex items-center justify-center rounded-xl bg-white/5 disabled:opacity-30"
          onClick={() => setDate((d) => shiftDate(range, d, 1))}><ChevronRight size={16} /></button>
        {!current && <button type="button" className="min-h-11 rounded-xl bg-white/5 px-3 text-sm" onClick={() => setDate(new Date())}>Today</button>}
        {isFetching && <Loader2 size={14} className="animate-spin text-slate-500" />}
      </div>

      {isLoading && <p className="flex items-center gap-2 text-slate-400"><Loader2 className="animate-spin" size={16} />Loading...</p>}
      {error && <p role="alert" className="text-red-400">{failureText(error, 'Could not load the energy numbers')}</p>}

      {data && (
        <>
          {data.grid.stats.length === 0 && data.utility.length === 0 && (
            <p className="text-slate-400">
              Nothing to show yet: no whole-house or utility source is set up.
              {isAdmin ? ' Open Settings to choose one, or import the Home Assistant energy settings.' : ''}
            </p>
          )}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <Kpi label="Used" value={formatKwh(data.grid.kwh)} tone="text-amber-300"
              hint={data.grid.returned_kwh ? `${formatKwh(data.grid.returned_kwh)} returned` : undefined} />
            <Kpi label="Cost" value={formatMoney(data.grid.cost, data.currency)}
              hint={data.grid.cost_source ? COST_SOURCE_LABEL[data.grid.cost_source] : 'no rate set'} />
            <Kpi label="Drawing now" value={current ? formatPower(data.grid.power_w) : '--'} tone="text-sky-300" />
            <Kpi label="Not on a circuit" value={formatKwh(data.untracked_kwh)} hint="whole house minus the devices" />
          </div>

          <section className="glass-card rounded-2xl p-4" aria-label="Usage">
            <h2 className="text-sm font-semibold text-slate-300 mb-2">Whole house</h2>
            <UsageChart summary={data} />
            {data.solar && <p className="text-sm text-slate-400 mt-2">Solar produced {formatKwh(data.solar.kwh)}</p>}
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="glass-card rounded-2xl p-4" aria-label="Devices">
              <h2 className="text-sm font-semibold text-slate-300 mb-2">By device and circuit</h2>
              <DeviceList summary={data} />
            </section>
            <div className="space-y-4">
              <section className="glass-card rounded-2xl p-4" aria-label="Time of use">
                <h2 className="text-sm font-semibold text-slate-300 mb-2">On-peak and off-peak</h2>
                <TouCard summary={data} isAdmin={isAdmin} openSettings={() => setSettingsOpen(true)} />
              </section>
              {data.utility.map((u) => (
                <section key={u.stat} className="glass-card rounded-2xl p-4 space-y-1" aria-label={u.name} data-testid="utility-card">
                  <h2 className="text-sm font-semibold text-slate-300">{u.name} (utility meter)</h2>
                  <p className="text-lg font-bold tabular-nums">{formatKwh(u.kwh)}
                    {u.cost != null && <span className="text-sm font-normal text-slate-400"> · {formatMoney(u.cost, data.currency ?? 'USD')}</span>}
                  </p>
                  {u.rate != null && <p className="text-xs text-slate-400">Average rate {formatMoney(u.rate, data.currency ?? 'USD')}/kWh over the last 30 days of readings</p>}
                  {u.data_until && (
                    <p className="text-xs text-slate-500">Readings until {new Date(u.data_until).toLocaleString()}; the utility publishes about a day late.</p>
                  )}
                </section>
              ))}
            </div>
          </div>
          <p className="text-xs text-slate-500">
            Looking for one circuit over time? <Link className="underline" to="/history">History</Link> plots any sensor.
          </p>
        </>
      )}
    </div>
  );
}
