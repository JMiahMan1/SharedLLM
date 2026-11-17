import { Battery, Crosshair, Home, MapPin, Pause, Play, Square } from 'lucide-react';
import { NotAvailable, OptionPicker, PANEL, WIDE, type ControlProps } from './primitives';
import { ImageTile, VacuumRooms } from '../../dashboards/EntityControls';
import { mapImage, vacuumFeatures, vacuumReadings } from '../../../lib/deviceControls';
import { isOff, plainName, readingText, words } from '../../../lib/deviceTiles';

const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : typeof v === 'string' && v !== '' && Number.isFinite(Number(v)) ? Number(v) : null);

/**
 * A robot vacuum (or mower): what it is doing and its battery, start/pause/stop/dock/locate as it
 * supports them (VacuumEntityFeature), suction (fan_speed_list), its map from the device's image
 * entity, room cleaning where the integration offers it, and its errors and wearing parts.
 */
export default function VacuumControl({ e, name, send, disabled, siblings }: ControlProps) {
  const f = vacuumFeatures(e);
  const r = vacuumReadings(siblings);
  const map = mapImage(siblings);
  const cleaning = e.state === 'cleaning' || e.state === 'mowing';
  const battery = num(e.attributes?.battery_level) ?? (r.battery && !isOff(r.battery) ? num(r.battery.state) : null);
  const status = typeof e.attributes?.status === 'string' && e.attributes.status ? e.attributes.status : words(e.state);
  const errors = r.errors.filter((x) => !isOff(x) && x.state !== 'off' && !/^(none|no error|ok|0)$/i.test(x.state));
  return (
    <div className="space-y-4" data-testid="vacuum-control">
      <div className={`${PANEL} flex items-center justify-between gap-3`}>
        <div className="min-w-0">
          <p className="truncate text-xl font-semibold text-white">{status}</p>
          <p className="text-xs text-slate-400">Status</p>
        </div>
        {battery != null && (
          <div className="flex shrink-0 items-center gap-1.5 text-white" aria-label={`Battery ${battery}%`}>
            <Battery size={20} className={battery < 20 ? 'text-rose-300' : 'text-emerald-300'} aria-hidden />
            <span className="text-xl font-semibold tabular-nums">{battery}%</span>
          </div>
        )}
      </div>
      {errors.length > 0 && (
        <div className="rounded-2xl border border-rose-400/25 bg-rose-500/10 p-3 text-sm text-rose-100" role="status" data-testid="vacuum-error">
          {errors.map((x) => <p key={x.entity_id}>{x.domain === 'binary_sensor' ? 'It reports a problem.' : words(x.state)}</p>)}
        </div>
      )}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {cleaning && f.pause ? (
          <button type="button" className={WIDE} disabled={disabled} aria-label={`Pause ${name}`} onClick={() => void send('pause')}>
            <Pause size={16} aria-hidden /> Pause</button>
        ) : f.start && (
          <button type="button" className={WIDE} disabled={disabled} aria-label={`Start ${name}`} onClick={() => void send('start')}>
            <Play size={16} aria-hidden /> {e.state === 'paused' ? 'Resume' : 'Start'}</button>
        )}
        {f.stop && <button type="button" className={WIDE} disabled={disabled} aria-label={`Stop ${name}`} onClick={() => void send('stop')}>
          <Square size={14} aria-hidden /> Stop</button>}
        {f.home && <button type="button" className={WIDE} disabled={disabled} aria-label={`Send ${name} to its dock`}
          onClick={() => void send('return_to_base')}><Home size={16} aria-hidden /> Dock</button>}
        {f.locate && <button type="button" className={WIDE} disabled={disabled} aria-label={`Find ${name}`}
          onClick={() => void send('locate')}><MapPin size={16} aria-hidden /> Find it</button>}
        {f.spot && <button type="button" className={WIDE} disabled={disabled} aria-label={`Spot clean with ${name}`}
          onClick={() => void send('clean_spot')}><Crosshair size={16} aria-hidden /> Spot</button>}
      </div>
      {f.fanSpeeds.length > 0 && (
        <OptionPicker label="Suction" options={f.fanSpeeds} value={String(e.attributes?.fan_speed ?? '')} disabled={disabled}
          testId="vacuum-fan" onPick={(v) => void send('set_fan_speed', { fan_speed: v })} />
      )}
      <div data-testid="vacuum-map">
        <p className="mb-1 text-xs font-medium text-slate-400">Map</p>
        {map && !isOff(map) ? <ImageTile entity={{ ...map, name: `${name} map` }} />
          : <NotAvailable testId="map-missing">{map ? 'The map is not available yet. It appears after the next clean.' : 'This vacuum does not share a map.'}</NotAvailable>}
      </div>
      {f.rooms && <VacuumRooms entity={e} />}
      {r.wear.length > 0 && (
        <div className={PANEL} data-testid="vacuum-wear">
          <p className="mb-2 text-xs font-medium text-slate-400">Parts</p>
          <dl className="grid grid-cols-2 gap-3">
            {r.wear.map((x) => (
              <div key={x.entity_id} className="min-w-0">
                <dt className="truncate text-xs text-slate-400">{plainName(x.name, name)}</dt>
                <dd className="text-sm font-semibold tabular-nums text-white">{isOff(x) ? 'Not known' : readingText(x)}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  );
}
