import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Download, Loader2, Plus, Sparkles, Trash2, X } from 'lucide-react';
import {
  MONTH_NAMES, WEEKDAY_NAMES, describePeriod, emptyPeriod, failureText, getEnergyCandidates, getEnergyPrefs,
  importEnergyFromHa, periodLabel, saveEnergyPrefs, suggestTariff, tariffPeriodError, toggleIn,
  type DeviceConsumption, type EnergyCandidate, type EnergyPrefs, type EnergySource, type TariffPeriod,
  type UtilitySource,
} from '../../lib/dashboards';

const INPUT = 'min-h-11 rounded-xl bg-white/5 border border-white/10 px-3 text-sm';
const BTN = 'min-h-11 rounded-xl px-3 text-sm flex items-center gap-1';

function numOrNull(v: string): number | null {
  if (v.trim() === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function StatSelect({ value, options, onChange, label, allowEmpty = false }: {
  value: string | null | undefined; options: EnergyCandidate[]; onChange: (v: string | null) => void; label: string;
  allowEmpty?: boolean;
}) {
  const known = options.some((o) => o.stat_energy === value);
  return (
    <select aria-label={label} className={`${INPUT} w-full`} value={value ?? ''} onChange={(e) => onChange(e.target.value || null)}>
      {(allowEmpty || !value) && <option value="">{allowEmpty ? 'None' : 'Choose...'}</option>}
      {value && !known && <option value={value}>{value}</option>}
      {options.map((o) => <option key={o.stat_energy} value={o.stat_energy}>{o.name || o.stat_energy}</option>)}
    </select>
  );
}

function PeriodEditor({ period, onChange, onRemove }: {
  period: TariffPeriod; onChange: (p: TariffPeriod) => void; onRemove: () => void;
}) {
  const error = tariffPeriodError(period);
  return (
    <div className="rounded-xl border border-white/10 p-3 space-y-2" data-testid="tariff-period">
      <div className="flex flex-wrap gap-2">
        <input aria-label="Period name" className={`${INPUT} flex-1 min-w-32`} placeholder="On-peak" value={period.name}
          onChange={(e) => onChange({ ...period, name: e.target.value })} />
        <input aria-label="Price per kWh" className={`${INPUT} w-28`} inputMode="decimal" placeholder="Price"
          value={period.price ?? ''} onChange={(e) => onChange({ ...period, price: numOrNull(e.target.value) })} />
        <input aria-label="Starts" type="time" className={INPUT} value={period.start}
          onChange={(e) => onChange({ ...period, start: e.target.value })} />
        <input aria-label="Ends" className={`${INPUT} w-24`} placeholder="HH:MM" value={period.end}
          onChange={(e) => onChange({ ...period, end: e.target.value })} />
        <button type="button" aria-label="Remove period" className={`${BTN} bg-white/5`} onClick={onRemove}><Trash2 size={14} /></button>
      </div>
      <div className="flex flex-wrap gap-1" role="group" aria-label="Weekdays">
        {WEEKDAY_NAMES.map((d, i) => (
          <button key={d} type="button" aria-pressed={period.weekdays.includes(i)}
            className={`min-h-9 rounded-lg px-2 text-xs ${period.weekdays.includes(i) ? 'bg-amber-500/25 text-amber-100' : 'bg-white/5'}`}
            onClick={() => onChange({ ...period, weekdays: toggleIn(period.weekdays, i) })}>{d}</button>
        ))}
      </div>
      <div className="flex flex-wrap gap-1" role="group" aria-label="Months">
        {MONTH_NAMES.map((m, i) => (
          <button key={m} type="button" aria-pressed={period.months.includes(i + 1)}
            className={`min-h-9 rounded-lg px-2 text-xs ${period.months.includes(i + 1) ? 'bg-sky-500/25 text-sky-100' : 'bg-white/5'}`}
            onClick={() => onChange({ ...period, months: toggleIn(period.months, i + 1) })}>{m}</button>
        ))}
      </div>
      <p className={`text-xs ${error ? 'text-red-400' : 'text-slate-500'}`}>{error ?? describePeriod(period)}</p>
    </div>
  );
}

/** The energy dashboard's configuration, as HA's energy settings had it, plus the rate plan (admins). */
export default function EnergySettings({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const { data: loaded, error, refetch } = useQuery({ queryKey: ['dashboard', 'energy-prefs'], queryFn: getEnergyPrefs });
  const { data: candidates } = useQuery({ queryKey: ['dashboard', 'energy-candidates'], queryFn: getEnergyCandidates });
  // Edits live here; until the first one the form shows what the hub has.
  const [edited, setPrefs] = useState<EnergyPrefs | null>(null);
  const prefs = edited ?? loaded ?? null;
  const [busy, setBusy] = useState<string | null>(null);

  const sensors = useMemo(() => (candidates ?? []).filter((c) => c.kind === 'sensor'), [candidates]);
  const utilities = useMemo(() => (candidates ?? []).filter((c) => c.kind === 'utility'), [candidates]);
  const everything = useMemo(() => [...utilities, ...sensors], [utilities, sensors]);

  if (error) return <p role="alert" className="text-red-400">{failureText(error, 'Could not load the energy settings')}</p>;
  if (!prefs) return <p className="text-slate-400 flex items-center gap-2"><Loader2 size={14} className="animate-spin" />Loading settings...</p>;

  const grids = prefs.energy_sources.filter((s) => s.type === 'grid');
  const others = prefs.energy_sources.filter((s) => s.type !== 'grid');
  const setGrids = (next: EnergySource[]) => setPrefs({ ...prefs, energy_sources: [...next, ...others] });
  const setDevices = (next: DeviceConsumption[]) => setPrefs({ ...prefs, device_consumption: next });
  const setUtils = (next: UtilitySource[]) => setPrefs({ ...prefs, utility_sources: next });
  const tariff = prefs.tariff;
  const setTariff = (patch: Partial<typeof tariff>) => setPrefs({ ...prefs, tariff: { ...tariff, ...patch } });
  const periodErrors = tariff.periods.map(tariffPeriodError).filter(Boolean);

  const save = async () => {
    setBusy('save');
    try {
      const saved = await saveEnergyPrefs(prefs);
      setPrefs(saved);
      toast.success('Energy settings saved');
      onSaved();
    } catch (err) {
      toast.error(failureText(err, 'Could not save'));
    } finally {
      setBusy(null);
    }
  };
  const suggest = async () => {
    setBusy('suggest');
    try {
      const s = await suggestTariff();
      if (!s.periods.length) toast('The utility data shows no on-peak hours');
      else {
        setTariff({ periods: s.periods });
        toast.success(`Filled ${s.periods.length} period(s) from ${s.hours_compared} hours of readings; add prices and save`);
      }
    } catch (err) {
      toast.error(failureText(err, 'Could not read the utility data'));
    } finally {
      setBusy(null);
    }
  };
  const importHa = async () => {
    setBusy('import');
    try {
      await importEnergyFromHa();
      toast.success('Imported the Home Assistant energy settings');
      setPrefs(null);
      await refetch();
      onSaved();
    } catch (err) {
      toast.error(failureText(err, 'Import failed'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="glass-card rounded-2xl p-4 space-y-5" aria-label="Energy settings" data-testid="energy-settings">
      <header className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Energy settings</h2>
        <button type="button" aria-label="Close settings" className={`${BTN} bg-white/5`} onClick={onClose}><X size={16} /></button>
      </header>

      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-slate-300">Whole house (grid)</h3>
        {grids.map((g, i) => (
          <div key={i} className="grid gap-2 sm:grid-cols-[1fr_1fr_8rem_auto] items-center">
            <StatSelect label="Energy from the grid" value={g.stat_energy_from} options={everything}
              onChange={(v) => setGrids(grids.map((x, j) => j === i ? { ...x, stat_energy_from: v } : x))} />
            <StatSelect label="Cost statistic" allowEmpty value={g.stat_cost} options={utilities.filter((u) => u.stat_cost).map((u) => ({ ...u, stat_energy: u.stat_cost as string, name: `${u.name} cost` }))}
              onChange={(v) => setGrids(grids.map((x, j) => j === i ? { ...x, stat_cost: v } : x))} />
            <input aria-label="Fixed price per kWh" className={INPUT} inputMode="decimal" placeholder="Price/kWh"
              value={g.number_energy_price ?? ''}
              onChange={(e) => setGrids(grids.map((x, j) => j === i ? { ...x, number_energy_price: numOrNull(e.target.value) } : x))} />
            <button type="button" aria-label="Remove grid source" className={`${BTN} bg-white/5`}
              onClick={() => setGrids(grids.filter((_, j) => j !== i))}><Trash2 size={14} /></button>
          </div>
        ))}
        <button type="button" className={`${BTN} bg-white/5`} onClick={() => setGrids([...grids, { type: 'grid', stat_energy_from: null }])}>
          <Plus size={14} />Add grid source
        </button>
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-slate-300">Individual devices and circuits</h3>
        {prefs.device_consumption.map((d, i) => (
          <div key={i} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto] items-center">
            <StatSelect label="Device energy" value={d.stat_consumption} options={sensors}
              onChange={(v) => setDevices(prefs.device_consumption.map((x, j) => j === i ? { ...x, stat_consumption: v ?? '' } : x))} />
            <input aria-label="Display name" className={INPUT} placeholder="Name" value={d.name ?? ''}
              onChange={(e) => setDevices(prefs.device_consumption.map((x, j) => j === i ? { ...x, name: e.target.value || null } : x))} />
            <button type="button" aria-label="Remove device" className={`${BTN} bg-white/5`}
              onClick={() => setDevices(prefs.device_consumption.filter((_, j) => j !== i))}><Trash2 size={14} /></button>
          </div>
        ))}
        <button type="button" className={`${BTN} bg-white/5`} onClick={() => setDevices([...prefs.device_consumption, { stat_consumption: '' }])}>
          <Plus size={14} />Add device
        </button>
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-slate-300">Utility meter readings</h3>
        {prefs.utility_sources.map((u, i) => (
          <div key={i} className="flex flex-wrap items-center gap-2">
            <input aria-label="Utility name" className={`${INPUT} flex-1`} value={u.name}
              onChange={(e) => setUtils(prefs.utility_sources.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
            <span className="text-xs text-slate-500">
              {u.stat_cost ? 'with cost' : 'no cost'}
              {u.stat_periods ? `, periods: ${Object.keys(u.stat_periods).map(periodLabel).join(', ')}` : ''}
            </span>
            <button type="button" aria-label="Remove utility" className={`${BTN} bg-white/5`}
              onClick={() => setUtils(prefs.utility_sources.filter((_, j) => j !== i))}><Trash2 size={14} /></button>
          </div>
        ))}
        {utilities.filter((c) => !prefs.utility_sources.some((u) => u.stat_energy === c.stat_energy)).map((c) => (
          <button key={c.stat_energy} type="button" className={`${BTN} bg-white/5`}
            onClick={() => setUtils([...prefs.utility_sources, {
              name: c.name || c.stat_energy, stat_energy: c.stat_energy, stat_cost: c.stat_cost ?? null, stat_periods: c.stat_periods ?? null,
            }])}><Plus size={14} />Add {c.name || c.stat_energy}</button>
        ))}
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-slate-300">Rate plan</h3>
        <div className="flex flex-wrap gap-2">
          <input aria-label="Currency" className={`${INPUT} w-24`} placeholder="USD" value={tariff.currency ?? ''}
            onChange={(e) => setTariff({ currency: e.target.value.toUpperCase() || null })} />
          <input aria-label="Flat price per kWh" className={`${INPUT} w-36`} inputMode="decimal" placeholder="Flat price/kWh"
            value={tariff.flat_price ?? ''} onChange={(e) => setTariff({ flat_price: numOrNull(e.target.value) })} />
          <input aria-label="Name for all other hours" className={`${INPUT} w-36`} value={tariff.default_period.name}
            onChange={(e) => setTariff({ default_period: { ...tariff.default_period, name: e.target.value } })} />
          <input aria-label="Price for all other hours" className={`${INPUT} w-36`} inputMode="decimal" placeholder="Their price/kWh"
            value={tariff.default_period.price ?? ''}
            onChange={(e) => setTariff({ default_period: { ...tariff.default_period, price: numOrNull(e.target.value) } })} />
        </div>
        <p className="text-xs text-slate-500">
          Time-of-use periods, first match wins; every other hour is "{tariff.default_period.name || 'Off-peak'}".
        </p>
        {tariff.periods.map((p, i) => (
          <PeriodEditor key={i} period={p}
            onChange={(np) => setTariff({ periods: tariff.periods.map((x, j) => j === i ? np : x) })}
            onRemove={() => setTariff({ periods: tariff.periods.filter((_, j) => j !== i) })} />
        ))}
        <div className="flex flex-wrap gap-2">
          <button type="button" className={`${BTN} bg-white/5`} onClick={() => setTariff({ periods: [...tariff.periods, emptyPeriod()] })}>
            <Plus size={14} />Add period
          </button>
          {prefs.utility_sources.some((u) => u.stat_periods && Object.keys(u.stat_periods).length) && (
            <button type="button" disabled={busy !== null} className={`${BTN} bg-violet-500/20 text-violet-100`} onClick={() => void suggest()}>
              {busy === 'suggest' ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}Suggest from utility data
            </button>
          )}
        </div>
      </div>

      <footer className="flex flex-wrap gap-2 border-t border-white/5 pt-4">
        <button type="button" disabled={busy !== null || periodErrors.length > 0} className={`${BTN} bg-amber-500/25 text-amber-100 disabled:opacity-50`}
          onClick={() => void save()}>{busy === 'save' && <Loader2 size={14} className="animate-spin" />}Save</button>
        <button type="button" disabled={busy !== null} className={`${BTN} bg-white/5`} onClick={() => void importHa()}>
          {busy === 'import' ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}Import from Home Assistant
        </button>
      </footer>
    </section>
  );
}
