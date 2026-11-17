import { useEffect, useRef, useState } from 'react';
import { Droplets, Minus, Plus, Thermometer } from 'lucide-react';
import { OptionPicker, PANEL, type ControlProps } from './primitives';
import { climateFeatures } from '../../../lib/deviceControls';
import { words } from '../../../lib/deviceTiles';

const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const MODE_WORDS: Record<string, string> = {
  heat_cool: 'Heat and cool', fan_only: 'Fan only', dry: 'Dry', auto: 'Auto', heat: 'Heat', cool: 'Cool', off: 'Off',
};
const DEBOUNCE_MS = 600;

/** One set point: minus, the number, plus; several taps are one command. */
function Stepper({ label, value, min, max, step, unit, disabled, onSet }: {
  label: string; value: number | null; min: number; max: number; step: number; unit: string; disabled: boolean;
  onSet: (v: number) => void;
}) {
  const [draft, setDraft] = useState<number | null>(null);
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  const shown = draft ?? value;
  const nudge = (dir: -1 | 1) => {
    if (shown == null) return;
    const next = Math.min(max, Math.max(min, Math.round((shown + dir * step) / step) * step));
    setDraft(next);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { timer.current = null; setDraft(null); onSet(next); }, DEBOUNCE_MS);
  };
  const btn = 'flex h-14 w-14 items-center justify-center rounded-full border border-white/10 bg-white/[0.06] text-white hover:bg-white/10 disabled:opacity-40';
  return (
    <div className="flex flex-col items-center gap-1" role="group" aria-label={label}>
      <p className="text-xs text-slate-400">{label}</p>
      <div className="flex items-center gap-3">
        <button type="button" className={btn} aria-label={`Lower ${label.toLowerCase()}`} disabled={disabled || shown == null || shown <= min}
          onClick={() => nudge(-1)}><Minus size={20} /></button>
        <span className="min-w-[5.5rem] text-center text-4xl font-semibold tabular-nums text-white" aria-live="polite"
          data-testid="setpoint">{shown == null ? '--' : `${Math.round(shown * 10) / 10}°`}</span>
        <button type="button" className={btn} aria-label={`Raise ${label.toLowerCase()}`} disabled={disabled || shown == null || shown >= max}
          onClick={() => nudge(1)}><Plus size={20} /></button>
      </div>
      <span className="sr-only">{unit}</span>
    </div>
  );
}

/**
 * A thermostat (or water heater): the room's temperature and humidity, the target (one set point,
 * or a low and high one for heat and cool), the modes it offers, and its fan, preset and swing
 * modes when it has them (ClimateEntityFeature).
 */
export default function ClimateControl({ e, send, disabled }: ControlProps) {
  const f = climateFeatures(e);
  const a = e.attributes ?? {};
  const current = num(a.current_temperature);
  const humidity = num(a.current_humidity);
  const action = typeof a.hvac_action === 'string' ? a.hvac_action : null;
  const off = e.state === 'off';
  const useRange = f.range && (e.state === 'heat_cool' || e.state === 'auto' || !f.single);
  return (
    <div className="space-y-4" data-testid="climate-control" data-entity={e.entity_id}>
      <div className={`${PANEL} flex flex-wrap items-center justify-between gap-3`}>
        <div className="flex items-center gap-2">
          <Thermometer size={20} className="text-orange-300" aria-hidden />
          <div>
            <p className="text-2xl font-semibold tabular-nums text-white">{current != null ? `${current}°` : '--'}</p>
            <p className="text-xs text-slate-400">Now{action ? ` · ${words(action)}` : ''}</p>
          </div>
        </div>
        {humidity != null && (
          <div className="flex items-center gap-2">
            <Droplets size={18} className="text-sky-300" aria-hidden />
            <div><p className="text-xl font-semibold tabular-nums text-white">{Math.round(humidity)}%</p><p className="text-xs text-slate-400">Humidity</p></div>
          </div>
        )}
      </div>
      {!off && (useRange ? (
        <div className="flex flex-wrap justify-center gap-6">
          <Stepper label="Heat to" value={num(a.target_temp_low)} min={f.min} max={f.max} step={f.step} unit={f.unit} disabled={disabled}
            onSet={(v) => void send('set_temperature', { target_temp_low: v, target_temp_high: num(a.target_temp_high) ?? f.max })} />
          <Stepper label="Cool to" value={num(a.target_temp_high)} min={f.min} max={f.max} step={f.step} unit={f.unit} disabled={disabled}
            onSet={(v) => void send('set_temperature', { target_temp_low: num(a.target_temp_low) ?? f.min, target_temp_high: v })} />
        </div>
      ) : f.single && (
        <Stepper label="Target" value={num(a.temperature)} min={f.min} max={f.max} step={f.step} unit={f.unit} disabled={disabled}
          onSet={(v) => void send('set_temperature', { temperature: v })} />
      ))}
      {f.modes.length > 0 && (
        <div data-testid="climate-modes">
          <p className="text-xs font-medium text-slate-400">Mode</p>
          <div role="group" aria-label="Mode" className="mt-1 flex flex-wrap gap-2">
            {f.modes.map((m) => (
              <button key={m} type="button" aria-pressed={e.state === m} disabled={disabled}
                onClick={() => void send('set_hvac_mode', { hvac_mode: m })}
                className={`min-h-11 rounded-xl border px-3.5 text-sm disabled:opacity-40 ${e.state === m
                  ? 'border-orange-400/60 bg-orange-500/15 font-semibold text-orange-100' : 'border-white/10 bg-white/[0.04] text-slate-200 hover:bg-white/10'}`}>
                {MODE_WORDS[m] ?? words(m)}
              </button>
            ))}
          </div>
        </div>
      )}
      <OptionPicker label="Fan" options={f.fanModes} value={String(a.fan_mode ?? '')} disabled={disabled}
        onPick={(v) => void send('set_fan_mode', { fan_mode: v })} />
      <OptionPicker label="Preset" options={f.presets} value={String(a.preset_mode ?? '')} disabled={disabled}
        onPick={(v) => void send('set_preset_mode', { preset_mode: v })} />
      <OptionPicker label="Swing" options={f.swingModes} value={String(a.swing_mode ?? '')} disabled={disabled}
        onPick={(v) => void send('set_swing_mode', { swing_mode: v })} />
    </div>
  );
}
