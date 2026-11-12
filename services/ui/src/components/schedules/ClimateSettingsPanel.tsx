import { useState } from 'react';
import toast from 'react-hot-toast';
import { Loader2, Plus, Trash2 } from 'lucide-react';
import Toggle from '../ui/Toggle';
import { Chip, EntityPicker, Field, MessageList } from '../automations/FormBits';
import { BUTTON, DANGER, INPUT, PRIMARY } from '../automations/styles';
import SetpointFields from './SetpointFields';
import { errorText } from '../../lib/errorText';
import { entityName, type Catalog } from '../../lib/automations';
import {
  blankSettings, isPowerSensor, mergeCapabilities, MONTH_LABELS, saveClimateSettings, toTimeInput, WEEKDAY_LABELS,
  WEEKDAYS,
  type ClimateSettings, type EnergyWindow, type Thermostat,
} from '../../lib/schedules';

/** `<input type="datetime-local">` value for an ISO moment, in local time. */
function toLocalInput(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

const numOrNull = (raw: string): number | null => (raw === '' ? null : Number(raw));

/**
 * Admins: away (a manual vacation with dates, or automatic when nobody is
 * home) and the energy rule (turn thermostats off when a power sensor stays
 * above a limit inside rate windows). Every person, sensor, thermostat and
 * window comes from the hub or is typed here; nothing is built in.
 */
export default function ClimateSettingsPanel({ settings, thermostats, catalog, onSaved }: {
  settings: ClimateSettings | null | undefined;
  thermostats: Thermostat[];
  catalog?: Catalog;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<ClimateSettings>(() => {
    const base = blankSettings();
    return { away: { ...base.away, ...(settings?.away ?? {}) }, energy: { ...base.energy, ...(settings?.energy ?? {}) } };
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const caps = mergeCapabilities(thermostats);
  const away = draft.away;
  const energy = draft.energy;
  const setAway = (patch: Partial<ClimateSettings['away']>) => setDraft({ ...draft, away: { ...away, ...patch } });
  const setEnergy = (patch: Partial<ClimateSettings['energy']>) => setDraft({ ...draft, energy: { ...energy, ...patch } });
  const setWindow = (i: number, patch: Partial<EnergyWindow>) =>
    setEnergy({ windows: energy.windows.map((w, j) => (j === i ? { ...w, ...patch } : w)) });
  const persons = catalog?.persons ?? [];

  async function save() {
    setBusy(true);
    setError('');
    try {
      await saveClimateSettings(draft);
      toast.success('Settings saved');
      onSaved();
    } catch (err) {
      setError(errorText(err, 'Saving did not work'));
    } finally {
      setBusy(false);
    }
  }

  const toggleIn = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

  return (
    <section className="glass-panel space-y-5 rounded-2xl border border-white/10 p-4" data-testid="climate-settings">
      <div className="space-y-3">
        <h2 className="text-[10px] font-black uppercase tracking-widest text-slate-400">Away</h2>
        <div className="flex min-h-11 items-center justify-between gap-3">
          <span className="text-sm text-slate-200">Vacation (set by hand)</span>
          <Toggle checked={away.enabled} ariaLabel="Vacation" onChange={(v) => setAway({ enabled: v })} />
        </div>
        {away.enabled && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="From">
              <input className={INPUT} type="datetime-local" value={toLocalInput(away.start)} onChange={(e) => setAway({ start: fromLocalInput(e.target.value) })} />
            </Field>
            <Field label="Until">
              <input className={INPUT} type="datetime-local" value={toLocalInput(away.end)} onChange={(e) => setAway({ end: fromLocalInput(e.target.value) })} />
            </Field>
          </div>
        )}
        <div className="flex min-h-11 items-center justify-between gap-3">
          <span className="text-sm text-slate-200">Away when nobody is home</span>
          <Toggle checked={away.auto} ariaLabel="Away when nobody is home" onChange={(v) => setAway({ auto: v })} />
        </div>
        {away.auto && (
          <div>
            <p className="mb-1 text-xs text-slate-400">Who counts (none chosen means everyone)</p>
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="People">
              {persons.map((p) => (
                <Chip key={p} active={away.persons.includes(p)} onClick={() => setAway({ persons: toggleIn(away.persons, p) })}>
                  {entityName(catalog, p)}
                </Chip>
              ))}
              {!persons.length && <span className="text-xs text-slate-500">The hub lists no people.</span>}
            </div>
          </div>
        )}
        <div>
          <p className="mb-1 text-xs text-slate-400">Thermostats away applies to (none chosen means all)</p>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Thermostats for away">
            {thermostats.map((t) => (
              <Chip key={t.entity_id} active={(away.thermostats ?? []).includes(t.entity_id)}
                onClick={() => setAway({ thermostats: toggleIn(away.thermostats ?? [], t.entity_id) })}>
                {t.name || t.entity_id}
              </Chip>
            ))}
          </div>
        </div>
        <div>
          <p className="mb-1 text-xs text-slate-400">While away</p>
          <SetpointFields value={away.setpoint} capabilities={caps} idPrefix="away" onChange={(sp) => setAway({ setpoint: sp })} />
        </div>
      </div>

      <div className="space-y-3 border-t border-white/5 pt-4">
        <h2 className="text-[10px] font-black uppercase tracking-widest text-slate-400">Energy</h2>
        <div className="flex min-h-11 items-center justify-between gap-3">
          <span className="text-sm text-slate-200">Turn thermostats off at high power use</span>
          <Toggle checked={energy.enabled} ariaLabel="Energy rule" onChange={(v) => setEnergy({ enabled: v })} />
        </div>
        <Field group label="Power sensor">
          <EntityPicker entities={catalog?.entities ?? []} filter={isPowerSensor} label="Power sensor"
            value={energy.power_sensor ? [energy.power_sensor] : []} onChange={(next) => setEnergy({ power_sensor: next[0] ?? null })} />
        </Field>
        <div>
          <p className="mb-1 text-xs text-slate-400">Thermostats it may turn off (none chosen means all)</p>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Thermostats for the energy rule">
            {thermostats.map((t) => (
              <Chip key={t.entity_id} active={energy.thermostats.includes(t.entity_id)}
                onClick={() => setEnergy({ thermostats: toggleIn(energy.thermostats, t.entity_id) })}>
                {t.name || t.entity_id}
              </Chip>
            ))}
          </div>
        </div>

        <p className="text-xs text-slate-400">Rate windows</p>
        {energy.windows.map((w, i) => (
          <div key={i} className="space-y-2 rounded-xl border border-white/10 p-3" data-testid="energy-window">
            <div className="flex items-end gap-2">
              <Field label="Name">
                <input className={INPUT} value={w.name} onChange={(e) => setWindow(i, { name: e.target.value })} />
              </Field>
              <button type="button" className={DANGER} aria-label={`Remove window ${w.name || i + 1}`}
                onClick={() => setEnergy({ windows: energy.windows.filter((_, j) => j !== i) })}>
                <Trash2 size={14} />
              </button>
            </div>
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Months">
              {MONTH_LABELS.map((m, mi) => (
                <Chip key={m} active={w.months.includes(mi + 1)}
                  onClick={() => setWindow(i, { months: w.months.includes(mi + 1) ? w.months.filter((x) => x !== mi + 1) : [...w.months, mi + 1].sort((a, b) => a - b) })}>
                  {m}
                </Chip>
              ))}
            </div>
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Window days">
              {WEEKDAYS.map((d) => (
                <Chip key={d} active={w.weekdays.includes(d)} onClick={() => setWindow(i, { weekdays: WEEKDAYS.filter((x) => (x === d ? !w.weekdays.includes(d) : w.weekdays.includes(x))) })}>
                  {WEEKDAY_LABELS[d]}
                </Chip>
              ))}
            </div>
            <div className="grid grid-cols-3 gap-2">
              <Field label="Starts">
                <input className={INPUT} type="time" value={toTimeInput(w.start)} onChange={(e) => setWindow(i, { start: e.target.value })} />
              </Field>
              <Field label="Ends">
                <input className={INPUT} type="time" value={toTimeInput(w.stop)} onChange={(e) => setWindow(i, { stop: e.target.value })} />
              </Field>
              <Field label="Limit (kW)">
                <input className={INPUT} type="number" inputMode="decimal" step="0.1" value={w.limit_kw ?? ''}
                  onChange={(e) => setWindow(i, { limit_kw: numOrNull(e.target.value) })} />
              </Field>
            </div>
          </div>
        ))}
        <button type="button" className={BUTTON}
          onClick={() => setEnergy({ windows: [...energy.windows, { name: '', months: [], weekdays: ['mon', 'tue', 'wed', 'thu', 'fri'], start: '', stop: '', limit_kw: null }] })}>
          <Plus size={14} /> Add window
        </button>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Field label="Off after over the limit for (min)">
            <input className={INPUT} type="number" inputMode="numeric" value={energy.shed_after_min}
              onChange={(e) => setEnergy({ shed_after_min: Number(e.target.value) || 0 })} />
          </Field>
          <Field label="Back on below (kW)">
            <input className={INPUT} type="number" inputMode="decimal" step="0.1" value={energy.resume_below_kw ?? ''}
              onChange={(e) => setEnergy({ resume_below_kw: numOrNull(e.target.value) })} />
          </Field>
          <Field label="Comfort limit (indoor)">
            <input className={INPUT} type="number" inputMode="decimal" step="0.5" value={energy.comfort_max_temp ?? ''}
              onChange={(e) => setEnergy({ comfort_max_temp: numOrNull(e.target.value) })} />
          </Field>
        </div>
        <div>
          <div className="flex min-h-11 items-center justify-between gap-3">
            <span className="text-xs text-slate-400">When too warm while off, bring it back to</span>
            <Toggle checked={!!energy.comfort_setpoint} ariaLabel="Comfort setpoint"
              onChange={(v) => setEnergy({ comfort_setpoint: v ? { hvac_mode: caps.hvac_modes.find((m) => m === 'cool') ?? caps.hvac_modes[0] ?? 'cool' } : null })} />
          </div>
          {energy.comfort_setpoint && (
            <SetpointFields value={energy.comfort_setpoint} capabilities={caps} idPrefix="comfort" onChange={(sp) => setEnergy({ comfort_setpoint: sp })} />
          )}
        </div>
      </div>

      {error && <MessageList tone="error" items={[error]} />}
      <button type="button" className={PRIMARY} disabled={busy} onClick={() => void save()}>
        {busy && <Loader2 size={14} className="animate-spin" />} Save settings
      </button>
    </section>
  );
}
