import { Plus } from 'lucide-react';
import BigSlider from './BigSlider';
import { AMBER, OptionPicker, PowerRow, type ControlProps } from './primitives';
import { PowerUsage } from './SwitchControl';
import { lightEffects } from '../../../lib/deviceControls';
import {
  COLOUR_PRESETS, hexToRgb, kelvinToRgb, lightColour, lightFeatures, rgbCss, rgbHex,
} from '../../../lib/deviceTiles';

const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/**
 * A light: power, then brightness, white (colour temperature) and colour, each only when its
 * supported_color_modes include it, then its effects (effect_list, LightEntityFeature.EFFECT).
 * Colour is a palette of common colours plus the system colour picker for any other.
 */
export default function LightControl({ e, name, send, disabled, siblings }: ControlProps) {
  const f = lightFeatures(e);
  const on = e.state === 'on';
  const a = e.attributes ?? {};
  const colour = lightColour(e);
  const accent = colour ? rgbCss(colour) : AMBER;
  const pct = num(a.brightness) != null ? Math.max(1, Math.round((num(a.brightness)! / 255) * 100)) : 100;
  const kelvin = num(a.color_temp_kelvin) ?? Math.round((f.minKelvin + f.maxKelvin) / 2);
  const mode = typeof a.color_mode === 'string' ? a.color_mode : null;
  const current = colour && mode !== 'color_temp' ? rgbHex(colour) : null;
  const warm = rgbCss(kelvinToRgb(f.minKelvin));
  const cool = rgbCss(kelvinToRgb(f.maxKelvin));
  const mid = rgbCss(kelvinToRgb((f.minKelvin + f.maxKelvin) / 2));
  const effects = lightEffects(e);
  return (
    <div className="space-y-3" data-testid="light-control">
      <PowerRow on={on} name={name} disabled={disabled} accent={accent}
        onToggle={() => void send(on ? 'turn_off' : 'turn_on')} />
      {f.brightness && (
        <BigSlider label="Brightness" ariaLabel={`${name} brightness`} value={on ? pct : 0} min={1} max={100}
          format={(v) => (on || v > 1 ? `${Math.round(v)}%` : 'Off')} fill={on ? accent : 'rgba(148,163,184,0.25)'}
          disabled={disabled} testId="brightness"
          onCommit={(v) => void send('turn_on', { brightness_pct: Math.round(v) })} />
      )}
      {f.colorTemp && (
        <BigSlider label="White" ariaLabel={`${name} colour temperature`} value={kelvin} min={f.minKelvin}
          max={f.maxKelvin} step={50} disabled={disabled} testId="colour-temp"
          track={`linear-gradient(90deg, ${warm}, ${mid}, ${cool})`}
          format={(v) => `${Math.round(v / 50) * 50} K`}
          onCommit={(v) => void send('turn_on', { color_temp_kelvin: Math.round(v) })} />
      )}
      {f.color && (
        <div role="group" aria-label={`${name} colour`} className="grid grid-cols-5 justify-items-center gap-y-2.5 pt-1"
          data-testid="colour-palette">
          {COLOUR_PRESETS.map((p) => {
            const hex = rgbHex(p.rgb);
            const picked = current === hex;
            return (
              <button key={p.label} type="button" aria-label={p.label} aria-pressed={picked} disabled={disabled}
                onClick={() => void send('turn_on', { rgb_color: p.rgb })}
                className={`h-11 w-11 rounded-full border-2 outline-none focus-visible:ring-2 focus-visible:ring-sky-400 disabled:opacity-40 ${
                  picked ? 'border-white shadow-[0_0_0_2px_rgba(255,255,255,0.25)]' : 'border-white/10'}`}
                style={{ backgroundColor: rgbCss(p.rgb) }} />
            );
          })}
          <label className={`relative flex h-11 w-11 cursor-pointer items-center justify-center rounded-full border-2 border-white/10 ${disabled ? 'opacity-40' : ''}`}
            style={{ backgroundImage: 'conic-gradient(red, yellow, lime, cyan, blue, magenta, red)' }} title="Any colour">
            <Plus size={16} className="text-white drop-shadow" aria-hidden />
            <input type="color" aria-label="Any colour" disabled={disabled} value={current ?? '#ffffff'}
              className="absolute -inset-0.5 h-11 w-11 cursor-pointer opacity-0"
              onChange={(ev) => { const rgb = hexToRgb(ev.target.value); if (rgb) void send('turn_on', { rgb_color: rgb }); }} />
          </label>
        </div>
      )}
      {effects.length > 0 && (
        <OptionPicker label="Effect" options={effects} value={String(a.effect ?? '')} disabled={disabled || !on}
          testId="light-effects" onPick={(v) => void send('turn_on', { effect: v })} />
      )}
      <PowerUsage siblings={siblings} />
    </div>
  );
}
