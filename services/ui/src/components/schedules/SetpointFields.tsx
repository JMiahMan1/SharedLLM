import { Field } from '../automations/FormBits';
import { INPUT } from '../automations/styles';
import { MODE_FALLBACK, modeVisual } from '../../lib/climate';
import { omit } from '../../lib/automations';
import { usesRange, type Setpoint, type ThermostatCapabilities } from '../../lib/schedules';

/**
 * Mode, temperature (or a low/high pair) and fan for a slot, a hold, or the
 * away and comfort setpoints. The modes, fan modes and bounds come from the
 * thermostats themselves; the generic mode list is only used when none is known.
 */
export default function SetpointFields({ value, onChange, capabilities, disabled, idPrefix = 'sp' }: {
  value: Setpoint;
  onChange: (next: Setpoint) => void;
  capabilities?: ThermostatCapabilities;
  disabled?: boolean;
  idPrefix?: string;
}) {
  const modes = capabilities?.hvac_modes?.length ? capabilities.hvac_modes : MODE_FALLBACK;
  const fans = capabilities?.fan_modes ?? [];
  const range = usesRange(value, capabilities);
  const bounds = { min: capabilities?.min_temp, max: capabilities?.max_temp };
  const unit = capabilities?.unit ?? '°';
  const num = (key: keyof Setpoint, raw: string) =>
    onChange(raw === '' ? omit(value, [key]) : { ...value, [key]: Number(raw) });

  const setMode = (mode: string) => {
    let next: Setpoint = { ...value, hvac_mode: mode };
    if (mode === 'off') {
      next = { hvac_mode: 'off', ...(value.fan_mode ? { fan_mode: value.fan_mode } : {}) };
    } else if (capabilities?.range && mode === 'heat_cool' && typeof value.temperature === 'number') {
      const t = value.temperature;
      next = { ...omit(next, ['temperature']), target_temp_low: t - 2, target_temp_high: t + 2 };
    } else if (mode !== 'heat_cool' && typeof value.target_temp_low === 'number') {
      next = { ...omit(next, ['target_temp_low', 'target_temp_high']), temperature: value.target_temp_low };
    }
    onChange(next);
  };

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <Field label="Mode">
        <select id={`${idPrefix}-mode`} className={INPUT} disabled={disabled} value={value.hvac_mode} onChange={(e) => setMode(e.target.value)}>
          {!modes.includes(value.hvac_mode) && <option value={value.hvac_mode}>{modeVisual(value.hvac_mode).label}</option>}
          {modes.map((m) => <option key={m} value={m}>{modeVisual(m).label}</option>)}
        </select>
      </Field>
      {value.hvac_mode !== 'off' && (range ? (
        <>
          <Field label={`Low (${unit})`}>
            <input className={INPUT} type="number" inputMode="decimal" step="0.5" disabled={disabled} min={bounds.min} max={bounds.max}
              value={value.target_temp_low ?? ''} onChange={(e) => num('target_temp_low', e.target.value)} />
          </Field>
          <Field label={`High (${unit})`}>
            <input className={INPUT} type="number" inputMode="decimal" step="0.5" disabled={disabled} min={bounds.min} max={bounds.max}
              value={value.target_temp_high ?? ''} onChange={(e) => num('target_temp_high', e.target.value)} />
          </Field>
        </>
      ) : (
        <Field label={`Temperature (${unit})`}>
          <input className={INPUT} type="number" inputMode="decimal" step="0.5" disabled={disabled} min={bounds.min} max={bounds.max}
            value={value.temperature ?? ''} onChange={(e) => num('temperature', e.target.value)} />
        </Field>
      ))}
      {fans.length > 0 && (
        <Field label="Fan">
          <select className={INPUT} disabled={disabled} value={value.fan_mode ?? ''}
            onChange={(e) => onChange(e.target.value ? { ...value, fan_mode: e.target.value } : omit(value, ['fan_mode']))}>
            <option value="">Leave as is</option>
            {fans.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        </Field>
      )}
    </div>
  );
}
