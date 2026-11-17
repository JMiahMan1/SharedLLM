import {
  AlertTriangle, ArrowDownUp, BellRing, ChevronDown, ChevronUp, Download, ExternalLink, Lock, LockOpen, RotateCw,
  Square, Warehouse,
} from 'lucide-react';
import BigSlider from './BigSlider';
import { GREEN, History, OptionPicker, PANEL, PowerRow, WIDE, type ControlProps } from './primitives';
import { CameraTile, EntityControl } from '../../dashboards/EntityControls';
import { confirmDialog } from '../../ui/confirmDialog';
import { cameraExtras, coverFeatures, fanFeatures, sirenFeatures } from '../../../lib/deviceControls';
import { updateInfo, updateLine } from '../../../lib/entityControls';
import { isOff, plainName, readingText, words } from '../../../lib/deviceTiles';

const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** Blinds, shades, curtains, valves: open, stop, close, and the position when it can be set. */
export function CoverControl({ e, name, send, disabled }: ControlProps) {
  const f = coverFeatures(e);
  const pos = num(e.attributes?.current_position);
  return (
    <div className="space-y-3" data-testid="cover-control">
      <p className="text-center text-xl font-semibold text-white">{pos != null && e.state === 'open' && pos < 100 ? `Open ${pos}%` : words(e.state)}</p>
      <div className="flex gap-2">
        {f.open && <button type="button" className={WIDE} disabled={disabled} aria-label={`Open ${name}`}
          onClick={() => void send('open_cover')}><ChevronUp size={18} aria-hidden /> Open</button>}
        {f.stop && <button type="button" className={WIDE} disabled={disabled} aria-label={`Stop ${name}`}
          onClick={() => void send('stop_cover')}><Square size={14} aria-hidden /> Stop</button>}
        {f.close && <button type="button" className={WIDE} disabled={disabled} aria-label={`Close ${name}`}
          onClick={() => void send('close_cover')}><ChevronDown size={18} aria-hidden /> Close</button>}
      </div>
      {f.position && pos != null && (
        <BigSlider label="Position" ariaLabel={`${name} position`} value={pos} min={0} max={100} disabled={disabled}
          format={(v) => (v === 0 ? 'Closed' : v === 100 ? 'Open' : `${Math.round(v)}% open`)} fill={GREEN}
          onCommit={(v) => void send('set_cover_position', { position: Math.round(v) })} />
      )}
    </div>
  );
}

/** A garage door or gate: one big button that says what it will do, and asks first. */
export function GarageControl({ e, name, send, disabled }: ControlProps) {
  const closed = e.state === 'closed' || e.state === 'closing';
  const moving = e.state === 'opening' || e.state === 'closing';
  const act = async () => {
    if (moving) { await send('stop_cover'); return; }
    if (await confirmDialog(closed
      ? { title: `Open ${name}?`, body: 'Check the doorway is clear before it moves.', confirmLabel: 'Open', danger: true }
      : { title: `Close ${name}?`, body: 'Check nothing is under the door before it moves.', confirmLabel: 'Close' })) {
      await send(closed ? 'open_cover' : 'close_cover');
    }
  };
  return (
    <div className="flex flex-col items-center gap-3 py-2" data-testid="garage-control">
      <button type="button" onClick={() => void act()} disabled={disabled}
        aria-label={moving ? `Stop ${name}` : closed ? `Open ${name}` : `Close ${name}`}
        className={`flex h-24 w-24 items-center justify-center rounded-full border outline-none focus-visible:ring-2 focus-visible:ring-sky-400 disabled:opacity-40 ${
          closed ? 'border-emerald-400/40 bg-emerald-500/15 text-emerald-200' : 'border-amber-400/40 bg-amber-500/15 text-amber-200'}`}>
        {moving ? <Square size={30} /> : <Warehouse size={36} />}
      </button>
      <p className="text-xl font-semibold text-white">{words(e.state)}</p>
      <p className="text-xs text-slate-400">{moving ? 'Tap to stop it.' : `Tap to ${closed ? 'open' : 'close'}. You will be asked to confirm.`}</p>
    </div>
  );
}

/** A lock: one big button, and a confirmation before it moves. */
export function LockControl({ e, name, send, disabled }: ControlProps) {
  const locked = e.state === 'locked' || e.state === 'locking';
  const jammed = e.state === 'jammed';
  const act = async () => {
    const ok = await confirmDialog(locked
      ? { title: `Unlock ${name}?`, body: 'Anyone at the door can open it once it is unlocked.', confirmLabel: 'Unlock', danger: true }
      : { title: `Lock ${name}?`, body: 'Check nobody is in the doorway first.', confirmLabel: 'Lock' });
    if (ok) await send(locked ? 'unlock' : 'lock');
  };
  return (
    <div className="flex flex-col items-center gap-3 py-2" data-testid="lock-control">
      <button type="button" onClick={() => void act()} disabled={disabled}
        aria-label={locked ? `Unlock ${name}` : `Lock ${name}`}
        className={`flex h-24 w-24 items-center justify-center rounded-full border outline-none focus-visible:ring-2 focus-visible:ring-sky-400 disabled:opacity-40 ${
          jammed ? 'border-rose-400/40 bg-rose-500/15 text-rose-200'
            : locked ? 'border-emerald-400/40 bg-emerald-500/15 text-emerald-200' : 'border-amber-400/40 bg-amber-500/15 text-amber-200'}`}>
        {locked ? <Lock size={36} /> : <LockOpen size={36} />}
      </button>
      <p className="text-xl font-semibold text-white">{jammed ? 'Jammed' : words(e.state)}</p>
      <p className="text-xs text-slate-400">Tap to {locked ? 'unlock' : 'lock'}. You will be asked to confirm.</p>
    </div>
  );
}

/** A siren: sound it (with a confirmation: it is loud) and stop it; its tone when it has several. */
export function SirenControl({ e, name, send, disabled }: ControlProps) {
  const f = sirenFeatures(e);
  const on = e.state === 'on';
  const sound = async () => {
    if (on) { await send('turn_off'); return; }
    if (await confirmDialog({ title: `Sound ${name}?`, body: 'It is loud, and everyone nearby will hear it.', confirmLabel: 'Sound it', danger: true })) {
      await send('turn_on');
    }
  };
  return (
    <div className="space-y-3" data-testid="siren-control">
      <button type="button" disabled={disabled || (on ? !f.off : !f.on)} onClick={() => void sound()}
        aria-label={on ? `Stop ${name}` : `Sound ${name}`}
        className={`flex min-h-14 w-full items-center justify-center gap-2 rounded-2xl border text-base font-semibold disabled:opacity-40 ${
          on ? 'border-rose-400/50 bg-rose-500/25 text-rose-100' : 'border-white/10 bg-white/[0.06] text-white hover:bg-white/10'}`}>
        <BellRing size={20} aria-hidden /> {on ? 'Stop the siren' : 'Sound the siren'}
      </button>
      {f.tones.length > 0 && (
        <OptionPicker label="Tone" options={f.tones} value={String(e.attributes?.tone ?? '')} disabled={disabled || on}
          onPick={(v) => void send('turn_on', { tone: v })} />
      )}
    </div>
  );
}

/** A fan: power, speed, oscillation, direction and presets as it supports them (FanEntityFeature). */
export function FanControl({ e, name, send, disabled }: ControlProps) {
  const f = fanFeatures(e);
  const on = e.state === 'on';
  const p = num(e.attributes?.percentage);
  const oscillating = e.attributes?.oscillating === true;
  const direction = String(e.attributes?.direction ?? 'forward');
  return (
    <div className="space-y-3" data-testid="fan-control">
      <PowerRow on={on} name={name} disabled={disabled} accent={GREEN} onToggle={() => void send(on ? 'turn_off' : 'turn_on')} />
      {f.speed && (
        <BigSlider label="Speed" ariaLabel={`${name} speed`} value={on ? p ?? 0 : 0} min={0} max={100}
          step={num(e.attributes?.percentage_step) ?? 1} format={(v) => `${Math.round(v)}%`} fill={GREEN} disabled={disabled}
          onCommit={(v) => void send('set_percentage', { percentage: Math.round(v) })} />
      )}
      <div className="flex gap-2">
        {f.oscillate && <button type="button" className={WIDE} aria-pressed={oscillating} disabled={disabled}
          onClick={() => void send('oscillate', { oscillating: !oscillating })}>
          <ArrowDownUp size={16} className="rotate-90" aria-hidden /> {oscillating ? 'Swinging' : 'Swing'}</button>}
        {f.direction && <button type="button" className={WIDE} disabled={disabled}
          onClick={() => void send('set_direction', { direction: direction === 'forward' ? 'reverse' : 'forward' })}>
          <RotateCw size={16} aria-hidden /> {direction === 'forward' ? 'Forward' : 'Reverse'}</button>}
      </div>
      <OptionPicker label="Preset" options={f.presets} value={String(e.attributes?.preset_mode ?? '')} disabled={disabled}
        onPick={(v) => void send('set_preset_mode', { preset_mode: v })} />
    </div>
  );
}

/** A camera: its picture (opening the Cameras page for the live view), then its siren and light. */
export function CameraControl({ e, name, call, disabled, siblings }: ControlProps) {
  const extras = cameraExtras(siblings);
  return (
    <div className="space-y-3" data-testid="camera-control">
      {isOff(e) ? (
        <div className={`${PANEL} text-center text-sm text-slate-400`}>The camera is not answering, so there is no picture.</div>
      ) : <CameraTile entity={{ ...e, name: `${name}: open the live view` }} />}
      {extras.map((x) => {
        const on = x.state === 'on';
        const label = plainName(x.name, name);
        if (x.domain === 'siren') {
          return (
            <button key={x.entity_id} type="button" disabled={disabled || isOff(x)}
              onClick={async () => {
                if (on) { await call('siren', 'turn_off', x.entity_id); return; }
                if (await confirmDialog({ title: `Sound ${label}?`, body: 'It is loud, and everyone nearby will hear it.', confirmLabel: 'Sound it', danger: true })) {
                  await call('siren', 'turn_on', x.entity_id);
                }
              }}
              className={`flex min-h-12 w-full items-center justify-center gap-2 rounded-2xl border text-sm font-semibold disabled:opacity-40 ${
                on ? 'border-rose-400/50 bg-rose-500/25 text-rose-100' : 'border-white/10 bg-white/[0.06] text-white'}`}>
              <BellRing size={18} aria-hidden /> {on ? `Stop ${label}` : `Sound ${label}`}
            </button>
          );
        }
        return (
          <PowerRow key={x.entity_id} on={on} name={label} label={`${label} on ${name}`} title={label} disabled={disabled || isOff(x)}
            testId="camera-extra" onToggle={() => void call(x.domain, on ? 'turn_off' : 'turn_on', x.entity_id)} />
        );
      })}
    </div>
  );
}

/** Firmware: what is installed and offered, the notes, and install (asked first) or skip. */
export function UpdateControl({ e, name, send, disabled }: ControlProps) {
  const u = updateInfo(e);
  const pct = num(e.attributes?.update_percentage) ?? (typeof e.attributes?.in_progress === 'number' ? e.attributes.in_progress : null);
  return (
    <div className={`${PANEL} space-y-2`} data-testid="firmware-control" data-entity={e.entity_id}>
      <p className="text-sm font-semibold text-white">{u.title || plainName(e.name, name)}</p>
      <p className="text-sm text-slate-300">{isOff(e) ? 'Not known' : updateLine(u)}{pct != null ? ` (${pct}%)` : ''}</p>
      {u.summary && <p className="text-xs text-slate-400">{u.summary}</p>}
      <div className="flex flex-wrap gap-2">
        {u.available && u.canInstall && !u.inProgress && (
          <button type="button" disabled={disabled} className="inline-flex min-h-11 items-center gap-1.5 rounded-xl bg-sky-500 px-4 text-sm font-semibold text-slate-950 disabled:opacity-40"
            aria-label={`Install ${u.latest ?? 'the update'} on ${e.name}`}
            onClick={async () => {
              if (await confirmDialog({ title: `Install ${u.latest ?? 'the update'}?`,
                body: `${name} restarts while it installs and cannot be used for a few minutes.`, confirmLabel: 'Install' })) {
                await send('install');
              }
            }}><Download size={16} aria-hidden /> Install</button>
        )}
        {u.available && !u.inProgress && (
          <button type="button" disabled={disabled} className="min-h-11 rounded-xl border border-white/10 px-3 text-sm text-slate-200"
            aria-label={`Skip ${u.latest ?? 'this version'} on ${e.name}`} onClick={() => void send('skip')}>Skip</button>
        )}
        {Boolean(u.skipped && u.latest && u.skipped === u.latest) && (
          <button type="button" disabled={disabled} className="min-h-11 rounded-xl border border-white/10 px-3 text-sm text-slate-200"
            aria-label={`Stop skipping ${u.latest} on ${e.name}`} onClick={() => void send('clear_skipped')}>Stop skipping</button>
        )}
        {u.releaseUrl && (
          <a href={u.releaseUrl} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 items-center gap-1 text-sm text-slate-300 underline">
            <ExternalLink size={14} aria-hidden /> Release notes</a>
        )}
      </div>
      {u.available && !u.canInstall && <p className="text-xs text-slate-500">Install it from the device&apos;s own app or page.</p>}
    </div>
  );
}

/** A reading: the value big, what it measures, and the last day when it is a number. */
export function SensorControl({ e }: ControlProps) {
  const numeric = e.domain === 'sensor' && e.state !== '' && Number.isFinite(Number(e.state));
  const problem = e.domain === 'binary_sensor' && e.state === 'on'
    && ['problem', 'smoke', 'gas', 'carbon_monoxide', 'moisture', 'safety', 'tamper'].includes(e.device_class ?? '');
  return (
    <div className={PANEL} data-testid="sensor-reading">
      <p className={`flex items-center gap-2 text-3xl font-semibold tabular-nums ${problem ? 'text-rose-200' : 'text-white'}`}>
        {problem && <AlertTriangle size={24} aria-hidden />}{isOff(e) ? 'Not known' : readingText(e)}
      </p>
      <p className="mt-0.5 text-xs text-slate-400">{e.device_class ? words(e.device_class) : 'Reading'}</p>
      {numeric && <History entityId={e.entity_id} unit={e.unit} />}
    </div>
  );
}

/** Buttons, numbers, selects, times, images, scenes and remotes: the compact controls the Rooms page uses. */
export function GenericControl({ e, name }: ControlProps) {
  return <div className={`${PANEL} py-2`}><EntityControl entity={{ ...e, name: plainName(e.name, name) }} /></div>;
}
