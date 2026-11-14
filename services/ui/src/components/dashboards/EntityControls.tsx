import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowDown, ArrowLeft, ArrowRight, ArrowUp, CameraOff, Cctv, ChevronDown, ChevronUp, Circle, DoorOpen, ExternalLink,
  House, ImageOff, Lightbulb, Lock, LockOpen, Minus, Pause, Play, Plus, Power, SkipForward, Undo2, Volume2,
} from 'lucide-react';
import { useMediaToken } from '../../lib/mediaToken';
import { useQuery } from '@tanstack/react-query';
import { cameraMediaUrl, cameraRotation, rotatedMediaStyle } from '../../lib/cameras';
import { api } from '../../services/api';
import {
  SETPOINT_DEBOUNCE_MS, modeVisual, numberAttr, setpointRange, snap, titleize, type ClimateAttributes,
} from '../../lib/climate';
import {
  binaryLabel, brightnessPct, can, formatState, isActive, isUnavailable, runService, supportsBrightness,
  unavailableLabel, type HubEntity,
} from '../../lib/dashboards';
import {
  commandLabel, formatNumber, imageMediaUrl, numberRange, remoteCommands, segmentCleanData, selectOptions, snapNumber,
  timeInputValue, timeServiceValue, updateInfo, updateLine, vacuumSegments,
} from '../../lib/entityControls';

const TOUCH = 'min-h-11 min-w-11';

/**
 * An optimistic copy of an entity: a control shows its new state at once and
 * falls back to what the hub reports on the next poll.
 */
function useOptimistic(entity: HubEntity, onChanged?: () => void) {
  // A patch only applies to the snapshot it was made on: the next poll's entity replaces it.
  const [patch, setPatch] = useState<{ base: HubEntity; change: Partial<HubEntity> } | null>(null);
  const change = patch && patch.base === entity ? patch.change : null;
  const shown: HubEntity = change
    ? { ...entity, ...change, attributes: { ...entity.attributes, ...(change.attributes ?? {}) } } : entity;
  const act = async (domain: string, service: string, data?: Record<string, unknown>, optimistic?: Partial<HubEntity>) => {
    if (optimistic) setPatch({ base: entity, change: optimistic });
    const ok = await runService(domain, service, entity.entity_id, data);
    if (!ok) setPatch(null);
    onChanged?.();
  };
  return { shown, act };
}

function Toggle({ on, label, disabled, onClick }: { on: boolean; label: string; disabled?: boolean; onClick: () => void }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled} onClick={onClick}
      className={`${TOUCH} relative inline-flex items-center justify-center rounded-full disabled:opacity-40`}>
      <span className={`block h-6 w-11 rounded-full transition-colors ${on ? 'bg-amber-500' : 'bg-slate-700'}`}>
        <span className={`block h-5 w-5 mt-0.5 rounded-full bg-white transition-transform ${on ? 'translate-x-5' : 'translate-x-0.5'}`} />
      </span>
    </button>
  );
}

function Row({ entity, children, detail }: { entity: HubEntity; children?: React.ReactNode; detail?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2 py-1" data-testid="entity-row" data-entity={entity.entity_id}>
      <div className="min-w-0">
        <p className="truncate text-sm text-slate-200" title={entity.entity_id}>{entity.name}</p>
        {detail && <div className="text-xs text-slate-500">{detail}</div>}
      </div>
      <div className="flex items-center gap-1 shrink-0">{children}</div>
    </div>
  );
}

export function LightControl({ entity, onChanged }: { entity: HubEntity; onChanged?: () => void }) {
  const { shown, act } = useOptimistic(entity, onChanged);
  const on = shown.state === 'on';
  const pct = brightnessPct(shown);
  const [draft, setDraft] = useState<number | null>(null);
  const dim = supportsBrightness(entity);
  return (
    <div>
      <Row entity={shown} detail={isUnavailable(shown.state) ? unavailableLabel(shown) : on && pct != null ? `${pct}%` : on ? 'On' : 'Off'}>
        <Lightbulb size={16} className={on ? 'text-amber-400' : 'text-slate-600'} aria-hidden />
        <Toggle on={on} label={`Toggle ${entity.name}`} disabled={isUnavailable(shown.state)}
          onClick={() => act('light', 'toggle', undefined, { state: on ? 'off' : 'on' })} />
      </Row>
      {dim && on && (
        <input type="range" min={1} max={100} value={draft ?? pct ?? 100} aria-label={`${entity.name} brightness`}
          className="w-full accent-amber-500"
          onChange={(e) => setDraft(Number(e.target.value))}
          onPointerUp={() => {
            if (draft == null) return;
            const value = draft;
            setDraft(null);
            void act('light', 'turn_on', { brightness_pct: value },
              { attributes: { brightness: Math.round((value / 100) * 255) } });
          }}
          onKeyUp={() => {
            if (draft == null) return;
            const value = draft;
            setDraft(null);
            void act('light', 'turn_on', { brightness_pct: value });
          }} />
      )}
    </div>
  );
}

export function ToggleControl({ entity, onChanged }: { entity: HubEntity; onChanged?: () => void }) {
  const { shown, act } = useOptimistic(entity, onChanged);
  const on = shown.state === 'on';
  return (
    <Row entity={shown} detail={formatState(shown)}>
      <Toggle on={on} label={`Toggle ${entity.name}`} disabled={isUnavailable(shown.state)}
        onClick={() => act(entity.domain, 'toggle', undefined, { state: on ? 'off' : 'on' })} />
    </Row>
  );
}

export function SceneControl({ entity }: { entity: HubEntity }) {
  return (
    <Row entity={entity}>
      <button type="button" className={`${TOUCH} rounded-lg px-3 bg-white/5 text-xs text-slate-200`}
        onClick={() => void runService('scene', 'turn_on', entity.entity_id)}>Activate</button>
    </Row>
  );
}

export function MediaControl({ entity, onChanged }: { entity: HubEntity; onChanged?: () => void }) {
  const { shown, act } = useOptimistic(entity, onChanged);
  const vol = numberAttr(shown.attributes?.volume_level);
  const [draft, setDraft] = useState<number | null>(null);
  const title = [shown.attributes?.media_title, shown.attributes?.media_artist].filter(Boolean).join(' - ');
  const off = shown.state === 'off' || isUnavailable(shown.state);
  return (
    <div>
      <Row entity={shown} detail={title || titleize(shown.state)}>
        {off ? (can(entity, 'power') &&
          <button type="button" aria-label={`Turn on ${entity.name}`} className={`${TOUCH} rounded-lg bg-white/5`}
            disabled={isUnavailable(shown.state)} onClick={() => act('media_player', 'turn_on')}>
            <Power size={16} className="mx-auto text-slate-300" />
          </button>
        ) : (
          <>
            {(can(entity, 'pause') || can(entity, 'play')) &&
            <button type="button" aria-label={shown.state === 'playing' ? `Pause ${entity.name}` : `Play ${entity.name}`}
              className={`${TOUCH} rounded-lg bg-white/5`}
              onClick={() => act('media_player', 'media_play_pause', undefined,
                { state: shown.state === 'playing' ? 'paused' : 'playing' })}>
              {shown.state === 'playing' ? <Pause size={16} className="mx-auto" /> : <Play size={16} className="mx-auto" />}
            </button>}
            {can(entity, 'next_track') &&
            <button type="button" aria-label={`Next on ${entity.name}`} className={`${TOUCH} rounded-lg bg-white/5`}
              onClick={() => act('media_player', 'media_next_track')}>
              <SkipForward size={16} className="mx-auto" />
            </button>}
          </>
        )}
      </Row>
      {!off && vol != null && can(entity, 'volume') && (
        <label className="flex items-center gap-2">
          <Volume2 size={14} className="text-slate-500" aria-hidden />
          <input type="range" min={0} max={100} value={Math.round((draft ?? vol) * 100)}
            aria-label={`${entity.name} volume`} className="w-full accent-violet-500"
            onChange={(e) => setDraft(Number(e.target.value) / 100)}
            onPointerUp={() => {
              if (draft == null) return;
              const value = draft;
              setDraft(null);
              void act('media_player', 'volume_set', { volume_level: value }, { attributes: { volume_level: value } });
            }} />
        </label>
      )}
    </div>
  );
}

export function CoverLockVacuumControl({ entity, onChanged }: { entity: HubEntity; onChanged?: () => void }) {
  const { shown, act } = useOptimistic(entity, onChanged);
  const btn = (label: string, service: string, icon: React.ReactNode, state?: string) => (
    <button type="button" aria-label={`${label} ${entity.name}`} className={`${TOUCH} rounded-lg bg-white/5 px-2`}
      disabled={isUnavailable(shown.state)} onClick={() => act(entity.domain, service, undefined, state ? { state } : undefined)}>
      {icon}
    </button>
  );
  let controls: React.ReactNode = null;
  if (entity.domain === 'cover') {
    controls = <>{can(entity, 'open') && btn('Open', 'open_cover', <ChevronUp size={16} className="mx-auto" />, 'opening')}
      {can(entity, 'close') && btn('Close', 'close_cover', <ChevronDown size={16} className="mx-auto" />, 'closing')}</>;
  } else if (entity.domain === 'lock') {
    controls = shown.state === 'locked'
      ? btn('Unlock', 'unlock', <LockOpen size={16} className="mx-auto" />, 'unlocking')
      : btn('Lock', 'lock', <Lock size={16} className="mx-auto" />, 'locking');
  } else if (entity.domain === 'vacuum') {
    controls = <>{shown.state === 'cleaning'
      ? can(entity, 'pause') && btn('Pause', 'pause', <Pause size={16} className="mx-auto" />, 'paused')
      : can(entity, 'vacuum_start') && btn('Start', 'start', <Play size={16} className="mx-auto" />, 'cleaning')}
      {can(entity, 'vacuum_return') && btn('Dock', 'return_to_base', <span className="text-xs">Dock</span>, 'returning')}</>;
  }
  const battery = numberAttr(shown.attributes?.battery_level);
  const row = <Row entity={shown} detail={`${titleize(shown.state)}${battery != null ? ` · ${battery}%` : ''}`}>{controls}</Row>;
  if (entity.domain === 'vacuum' && can(entity, 'clean_area', false)) {
    return <div>{row}<VacuumRooms entity={shown} onChanged={onChanged} /></div>;
  }
  return row;
}

const CHIP = 'min-h-11 rounded-lg border px-3 text-xs';
const chipTone = (on: boolean) => (on ? 'border-amber-400/60 bg-amber-500/15 text-amber-100' : 'border-white/10 bg-white/5 text-slate-200');

/** Clean the rooms you pick: the vacuum's own rooms (`segments`), sent with `vacuum.send_command`. */
function VacuumRooms({ entity, onChanged }: { entity: HubEntity; onChanged?: () => void }) {
  const rooms = vacuumSegments(entity);
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  if (!rooms.length) return null;
  const toggle = (id: number) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const clean = async () => {
    setBusy(true);
    const ok = await runService('vacuum', 'send_command', entity.entity_id, segmentCleanData(picked));
    setBusy(false);
    if (ok) { setPicked([]); setOpen(false); }
    onChanged?.();
  };
  return (
    <div className="pb-1" data-testid="vacuum-rooms">
      <button type="button" className={`${CHIP} ${chipTone(open)}`} aria-expanded={open}
        disabled={isUnavailable(entity.state)} onClick={() => setOpen((v) => !v)}>
        Clean rooms
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          <div className="flex flex-wrap gap-1.5" role="group" aria-label={`Rooms for ${entity.name}`}>
            {rooms.map((r) => (
              <button key={r.id} type="button" aria-pressed={picked.includes(r.id)}
                className={`${CHIP} max-w-full truncate ${chipTone(picked.includes(r.id))}`} onClick={() => toggle(r.id)}>
                {r.name}
              </button>
            ))}
          </div>
          <button type="button" disabled={!picked.length || busy}
            className={`${TOUCH} rounded-lg bg-amber-500 px-3 text-xs font-semibold text-slate-950 disabled:opacity-40`}
            onClick={() => void clean()}>
            {picked.length ? `Clean ${picked.length} room${picked.length === 1 ? '' : 's'}` : 'Pick rooms to clean'}
          </button>
        </div>
      )}
    </div>
  );
}

/** A button entity: pressing it is the whole job (a doorbell chime, a restart, identify). */
export function ButtonControl({ entity, onChanged }: { entity: HubEntity; onChanged?: () => void }) {
  const [busy, setBusy] = useState(false);
  const off = entity.state === 'unavailable';
  return (
    <Row entity={entity} detail={off ? unavailableLabel(entity) : undefined}>
      <button type="button" aria-label={`Press ${entity.name}`} disabled={off || busy}
        className={`${TOUCH} rounded-lg bg-white/5 px-3 text-xs text-slate-200 disabled:opacity-40`}
        onClick={async () => { setBusy(true); await runService(entity.domain, 'press', entity.entity_id); setBusy(false); onChanged?.(); }}>
        Press
      </button>
    </Row>
  );
}

/** A number entity: a stepper always, and a slider when the device says it is one (or the range is small). */
export function NumberControl({ entity, onChanged }: { entity: HubEntity; onChanged?: () => void }) {
  const { shown, act } = useOptimistic(entity, onChanged);
  const r = numberRange(entity);
  const current = numberAttr(shown.state) ?? null;
  const [draft, setDraft] = useState<number | null>(null);
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  const off = shown.state === 'unavailable';
  const value = draft ?? current;
  const send = (next: number) => {
    const v = snapNumber(next, r);
    setDraft(null);
    void act('number', 'set_value', { value: v }, { state: String(v) });
  };
  const nudge = (dir: -1 | 1) => {
    const next = snapNumber((value ?? r.min) + dir * r.step, r);
    setDraft(next);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { timer.current = null; send(next); }, SETPOINT_DEBOUNCE_MS);
  };
  return (
    <div>
      <Row entity={shown} detail={off ? unavailableLabel(shown) : undefined}>
        <button type="button" aria-label={`Lower ${entity.name}`} disabled={off || (value != null && value <= r.min)}
          className={`${TOUCH} rounded-lg bg-white/5 disabled:opacity-40`} onClick={() => nudge(-1)}>
          <Minus size={16} className="mx-auto" />
        </button>
        <span className="min-w-14 text-center text-sm tabular-nums text-slate-100" aria-label={`${entity.name} value`}>
          {formatNumber(value, r)}
        </span>
        <button type="button" aria-label={`Raise ${entity.name}`} disabled={off || (value != null && value >= r.max)}
          className={`${TOUCH} rounded-lg bg-white/5 disabled:opacity-40`} onClick={() => nudge(1)}>
          <Plus size={16} className="mx-auto" />
        </button>
      </Row>
      {r.mode === 'slider' && !off && (
        <input type="range" min={r.min} max={r.max} step={r.step} value={value ?? r.min} aria-label={`${entity.name} level`}
          className="h-11 w-full accent-amber-500"
          onChange={(e) => setDraft(Number(e.target.value))}
          onPointerUp={() => { if (draft != null) send(draft); }}
          onKeyUp={() => { if (draft != null) send(draft); }} />
      )}
    </div>
  );
}

/** A select entity: its options in a dropdown. */
export function SelectControl({ entity, onChanged }: { entity: HubEntity; onChanged?: () => void }) {
  const { shown, act } = useOptimistic(entity, onChanged);
  const options = selectOptions(entity);
  const off = shown.state === 'unavailable' || !options.length;
  const known = options.includes(shown.state);
  return (
    <Row entity={shown} detail={shown.state === 'unavailable' ? unavailableLabel(shown) : undefined}>
      <select aria-label={entity.name} disabled={off} value={known ? shown.state : ''}
        className="min-h-11 max-w-[11rem] truncate rounded-lg border border-white/10 bg-black/30 px-2 text-xs text-slate-100 disabled:opacity-40"
        onChange={(e) => void act('select', 'select_option', { option: e.target.value }, { state: e.target.value })}>
        {!known && <option value="" disabled>{isUnavailable(shown.state) ? 'Unknown' : shown.state}</option>}
        {options.map((o) => <option key={o} value={o}>{titleize(o)}</option>)}
      </select>
    </Row>
  );
}

/** A time entity: a time of day, saved with Set (a picker changes its value while you scroll it). */
export function TimeControl({ entity, onChanged }: { entity: HubEntity; onChanged?: () => void }) {
  const { shown, act } = useOptimistic(entity, onChanged);
  const saved = timeInputValue(shown.state);
  const [draft, setDraft] = useState<string | null>(null);
  const off = shown.state === 'unavailable';
  const value = draft ?? saved;
  const dirty = draft != null && draft !== saved && timeServiceValue(draft) != null;
  return (
    <Row entity={shown} detail={off ? unavailableLabel(shown) : undefined}>
      <input type="time" aria-label={entity.name} disabled={off} value={value}
        className="min-h-11 rounded-lg border border-white/10 bg-black/30 px-2 text-sm text-slate-100 disabled:opacity-40"
        onChange={(e) => setDraft(e.target.value)} />
      {dirty && (
        <button type="button" aria-label={`Set ${entity.name}`}
          className={`${TOUCH} rounded-lg bg-amber-500 px-3 text-xs font-semibold text-slate-950`}
          onClick={() => {
            const time = timeServiceValue(draft ?? '');
            setDraft(null);
            if (time) void act('time', 'set_value', { time }, { state: time });
          }}>
          Set
        </button>
      )}
    </Row>
  );
}

/** Keys a remote's pad shows, by the names its driver lists (first match wins: "select", "ok" or "enter"). */
const PAD: { slot: string; names: string[]; label: string; Icon: typeof ArrowUp }[] = [
  { slot: 'up', names: ['up'], label: 'Up', Icon: ArrowUp },
  { slot: 'left', names: ['left'], label: 'Left', Icon: ArrowLeft },
  { slot: 'ok', names: ['select', 'ok', 'enter'], label: 'OK', Icon: Circle },
  { slot: 'right', names: ['right'], label: 'Right', Icon: ArrowRight },
  { slot: 'down', names: ['down'], label: 'Down', Icon: ArrowDown },
  { slot: 'back', names: ['back', 'return'], label: 'Back', Icon: Undo2 },
  { slot: 'home', names: ['home'], label: 'Home', Icon: House },
];

/** A remote: power, its activities, a pad of the keys it knows, and any other command it takes. */
export function RemoteControl({ entity, onChanged }: { entity: HubEntity; onChanged?: () => void }) {
  const { shown, act } = useOptimistic(entity, onChanged);
  const { commands, activities } = remoteCommands(entity);
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState('');
  const [picked, setPicked] = useState('');
  const off = shown.state === 'unavailable';
  const send = (command: string) => act('remote', 'send_command', { command });
  const keys = PAD.flatMap((k) => {
    const name = k.names.find((n) => commands.includes(n));
    return name ? [{ ...k, name }] : [];
  });
  const current = typeof shown.attributes?.current_activity === 'string' ? shown.attributes.current_activity : '';
  return (
    <div data-testid="remote-control" data-entity={entity.entity_id}>
      <Row entity={shown} detail={off ? unavailableLabel(shown) : current || titleize(shown.state)}>
        <Toggle on={shown.state === 'on'} label={`Toggle ${entity.name}`} disabled={off}
          onClick={() => act('remote', shown.state === 'on' ? 'turn_off' : 'turn_on', undefined,
            { state: shown.state === 'on' ? 'off' : 'on' })} />
        <button type="button" aria-expanded={open} disabled={off} onClick={() => setOpen((v) => !v)}
          className={`${CHIP} ${chipTone(open)} disabled:opacity-40`}>Keys</button>
      </Row>
      {open && !off && (
        <div className="space-y-2 pb-2">
          {activities.length > 0 && (
            <select aria-label={`${entity.name} activity`} value={activities.includes(current) ? current : ''}
              className="min-h-11 w-full rounded-lg border border-white/10 bg-black/30 px-2 text-sm text-slate-100"
              onChange={(e) => void act('remote', 'turn_on', { activity: e.target.value },
                { state: 'on', attributes: { current_activity: e.target.value } })}>
              <option value="" disabled>Start an activity</option>
              {activities.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          )}
          {keys.length > 0 && (
            <div className="grid grid-cols-3 gap-1.5 max-w-[15rem] mx-auto" role="group" aria-label={`${entity.name} keys`}>
              {['', 'up', '', 'left', 'ok', 'right', 'back', 'down', 'home'].map((slot, i) => {
                const k = keys.find((x) => x.slot === slot);
                if (!k) return <span key={`gap-${i}`} aria-hidden />;
                return (
                  <button key={k.slot} type="button" aria-label={`${k.label} on ${entity.name}`}
                    className="min-h-11 rounded-lg bg-white/5 text-slate-200 active:bg-white/15" onClick={() => void send(k.name)}>
                    <k.Icon size={16} className="mx-auto" aria-hidden />
                  </button>
                );
              })}
            </div>
          )}
          {commands.length > 0 ? (
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (picked) void send(picked); }}>
              <select aria-label={`${entity.name} command`} value={picked} onChange={(e) => setPicked(e.target.value)}
                className="min-h-11 min-w-0 flex-1 rounded-lg border border-white/10 bg-black/30 px-2 text-sm text-slate-100">
                <option value="" disabled>Choose a key</option>
                {commands.map((c) => <option key={c} value={c}>{commandLabel(c)}</option>)}
              </select>
              <button type="submit" disabled={!picked} className={`${TOUCH} rounded-lg bg-white/10 px-3 text-xs text-slate-100 disabled:opacity-40`}>Send</button>
            </form>
          ) : (
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) { void send(typed.trim()); setTyped(''); } }}>
              <input aria-label={`${entity.name} command`} value={typed} onChange={(e) => setTyped(e.target.value)}
                placeholder="Command to send" autoCapitalize="none" autoCorrect="off"
                className="min-h-11 min-w-0 flex-1 rounded-lg border border-white/10 bg-black/30 px-3 text-sm text-slate-100" />
              <button type="submit" disabled={!typed.trim()} className={`${TOUCH} rounded-lg bg-white/10 px-3 text-xs text-slate-100 disabled:opacity-40`}>Send</button>
            </form>
          )}
          <Link to="/remote" className="inline-flex min-h-11 items-center text-xs text-slate-400 underline">Full remote</Link>
        </div>
      )}
    </div>
  );
}

/** An update entity: what is installed, what is offered, release notes, and install or skip where the hub can. */
export function UpdateControl({ entity, onChanged }: { entity: HubEntity; onChanged?: () => void }) {
  const { shown, act } = useOptimistic(entity, onChanged);
  const u = updateInfo(shown);
  const skipped = Boolean(u.skipped && u.latest && u.skipped === u.latest);
  const off = shown.state === 'unavailable';
  return (
    <div data-testid="update-control" data-entity={entity.entity_id}>
      <Row entity={shown} detail={off ? unavailableLabel(shown) : updateLine(u)} />
      {!off && (
        <div className="flex flex-wrap items-center gap-2 pb-1">
          {u.available && u.canInstall && !u.inProgress && (
            <button type="button" aria-label={`Install ${u.latest ?? 'the update'} on ${entity.name}`}
              className={`${TOUCH} rounded-lg bg-amber-500 px-3 text-xs font-semibold text-slate-950`}
              onClick={() => void act('update', 'install', undefined, { attributes: { in_progress: true } })}>
              Install
            </button>
          )}
          {u.available && !u.inProgress && (
            <button type="button" aria-label={`Skip ${u.latest ?? 'this version'} on ${entity.name}`}
              className={`${TOUCH} rounded-lg bg-white/5 px-3 text-xs text-slate-200`}
              onClick={() => void act('update', 'skip', undefined, { state: 'off', attributes: { skipped_version: u.latest } })}>
              Skip
            </button>
          )}
          {skipped && (
            <button type="button" aria-label={`Stop skipping ${u.latest} on ${entity.name}`}
              className={`${TOUCH} rounded-lg bg-white/5 px-3 text-xs text-slate-200`}
              onClick={() => void act('update', 'clear_skipped', undefined, { state: 'on', attributes: { skipped_version: null } })}>
              Stop skipping
            </button>
          )}
          {u.releaseUrl && (
            <a href={u.releaseUrl} target="_blank" rel="noopener noreferrer"
              className="inline-flex min-h-11 items-center gap-1 text-xs text-slate-300 underline">
              <ExternalLink size={12} aria-hidden /> Release notes
            </a>
          )}
          {u.available && !u.canInstall && (
            <span className="text-[11px] text-slate-500">Install it from the device's own app or page.</span>
          )}
        </div>
      )}
      {u.summary && <p className="pb-1 text-[11px] text-slate-500 break-words">{u.summary}</p>}
    </div>
  );
}

/** An image entity (a vacuum's map): its picture, fetched through the gateway with the signed media token. */
export function ImageTile({ entity }: { entity: HubEntity }) {
  const token = useMediaToken();
  const [failed, setFailed] = useState<string | null>(null);
  const url = imageMediaUrl(token, entity.entity_id, entity.state);
  return (
    <figure className="overflow-hidden rounded-xl border border-white/10 bg-black/40 my-1" data-testid="image-tile"
      data-entity={entity.entity_id}>
      <div className="relative flex min-h-32 items-center justify-center">
        {url && failed !== url ? (
          <img src={url} alt={entity.name} loading="lazy" className="block max-h-80 w-full object-contain"
            onError={() => setFailed(url)} />
        ) : (
          <div className="flex flex-col items-center gap-1 p-4 text-center text-xs text-slate-500">
            <ImageOff size={22} aria-hidden />
            {failed ? 'No picture right now' : 'Loading the picture'}
          </div>
        )}
      </div>
      <figcaption className="px-2 py-1 text-xs text-slate-300 truncate">{entity.name}</figcaption>
    </figure>
  );
}

/** A thermostat: current and target, a stepper, and the modes the entity offers. */
export function ClimateControl({ entity, onChanged, compact = false }: {
  entity: HubEntity; onChanged?: () => void; compact?: boolean;
}) {
  const { shown, act } = useOptimistic(entity, onChanged);
  const attrs = shown.attributes as ClimateAttributes;
  const { min, max, step } = setpointRange(attrs);
  const dual = shown.state === 'heat_cool' && attrs.temperature == null
    && attrs.target_temp_low != null && attrs.target_temp_high != null;
  const [pending, setPending] = useState<number | null>(null);
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  const target = pending ?? numberAttr(attrs.temperature);
  const unavailable = isUnavailable(shown.state);
  const modes = Array.isArray(attrs.hvac_modes) && attrs.hvac_modes.length ? attrs.hvac_modes : [];

  const nudge = (dir: -1 | 1) => {
    if (target == null) return;
    const next = snap(target + dir * step, min, max, step);
    setPending(next);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      void act('climate', 'set_temperature', { temperature: next }, { attributes: { temperature: next } })
        .finally(() => setPending(null));
    }, SETPOINT_DEBOUNCE_MS);
  };

  const current = numberAttr(attrs.current_temperature);
  const humidity = numberAttr(shown.attributes?.current_humidity) ?? numberAttr(attrs.humidity);
  return (
    <div className="space-y-2" data-testid="climate-control" data-entity={entity.entity_id}>
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          {!compact && <p className="truncate text-sm text-slate-200">{shown.name}</p>}
          <p className="text-xs text-slate-500">
            {unavailable ? unavailableLabel(shown) : [
              attrs.hvac_action ? titleize(attrs.hvac_action) : modeVisual(shown.state).label,
              current != null ? `Now ${current}°` : null,
              humidity != null ? `${Math.round(humidity)}% RH` : null,
            ].filter(Boolean).join(' · ')}
          </p>
        </div>
        {!dual && target != null && (
          <div className="flex items-center gap-1">
            <button type="button" aria-label={`Lower ${entity.name}`} disabled={unavailable}
              className={`${TOUCH} rounded-lg bg-white/5`} onClick={() => nudge(-1)}><Minus size={16} className="mx-auto" /></button>
            <span className="w-14 text-center text-lg font-semibold tabular-nums" aria-label={`${entity.name} target`}>
              {Math.round(target * 10) / 10}°
            </span>
            <button type="button" aria-label={`Raise ${entity.name}`} disabled={unavailable}
              className={`${TOUCH} rounded-lg bg-white/5`} onClick={() => nudge(1)}><Plus size={16} className="mx-auto" /></button>
          </div>
        )}
        {dual && (
          <span className="text-sm tabular-nums text-slate-300">
            {numberAttr(attrs.target_temp_low)}° - {numberAttr(attrs.target_temp_high)}°
          </span>
        )}
      </div>
      {modes.length > 0 && (
        <div className="flex flex-wrap gap-1" role="group" aria-label={`${entity.name} mode`}>
          {modes.map((m) => {
            const v = modeVisual(m);
            const active = shown.state === m;
            return (
              <button key={m} type="button" aria-pressed={active} disabled={unavailable}
                className={`min-h-9 rounded-lg px-2 text-xs flex items-center gap-1 border ${active ? 'border-white/30 bg-white/10' : 'border-white/5 bg-white/5'}`}
                onClick={() => act('climate', 'set_hvac_mode', { hvac_mode: m }, { state: m })}>
                <v.Icon size={13} className={v.tone} aria-hidden />{v.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Sensor chips: the reading at a glance. */
export function SensorChip({ entity }: { entity: HubEntity }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-white/5 border border-white/10 px-2.5 py-1 text-xs"
      title={entity.entity_id} data-testid="sensor-chip">
      <span className="text-slate-400">{entity.name}</span>
      <span className="text-slate-100 tabular-nums">{entity.domain === 'binary_sensor' ? binaryLabel(entity) : formatState(entity)}</span>
    </span>
  );
}

export function CameraTile({ entity }: { entity: HubEntity }) {
  const token = useMediaToken();
  const [failed, setFailed] = useState(false);
  const url = cameraMediaUrl(token, entity.entity_id, 'snapshot');
  // The same list the Cameras page reads (shared cache), for how far to turn this camera's picture.
  const cameras = useQuery({ queryKey: ['cameras'], queryFn: () => api.getCameras(), staleTime: 60_000 });
  const rotation = cameraRotation(cameras.data?.find((c) => c.entity_id === entity.entity_id));
  return (
    <Link to={`/cameras?camera=${encodeURIComponent(entity.entity_id)}`}
      className="block overflow-hidden rounded-xl border border-white/10 bg-black/40" aria-label={`Open ${entity.name}`}>
      <div className="relative aspect-video overflow-hidden flex items-center justify-center">
        {url && !failed ? (
          <img src={url} alt={entity.name} loading="lazy" className="absolute inset-0 h-full w-full object-cover"
            style={rotatedMediaStyle(rotation)} onError={() => setFailed(true)} />
        ) : failed ? <CameraOff size={22} className="text-slate-600" /> : <Cctv size={22} className="text-slate-600" />}
      </div>
      <p className="px-2 py-1 text-xs text-slate-300 truncate">{entity.name}</p>
    </Link>
  );
}

/** The right control for any entity a room card shows. */
export function EntityControl({ entity, onChanged }: { entity: HubEntity; onChanged?: () => void }) {
  switch (entity.domain) {
    case 'light': return <LightControl entity={entity} onChanged={onChanged} />;
    case 'switch': case 'fan': case 'siren': case 'humidifier': case 'input_boolean':
      return <ToggleControl entity={entity} onChanged={onChanged} />;
    case 'climate': return <ClimateControl entity={entity} onChanged={onChanged} />;
    case 'media_player': return <MediaControl entity={entity} onChanged={onChanged} />;
    case 'cover': case 'lock': case 'vacuum': return <CoverLockVacuumControl entity={entity} onChanged={onChanged} />;
    case 'scene': return <SceneControl entity={entity} />;
    case 'remote': return <RemoteControl entity={entity} onChanged={onChanged} />;
    case 'button': case 'input_button': return <ButtonControl entity={entity} onChanged={onChanged} />;
    case 'number': return <NumberControl entity={entity} onChanged={onChanged} />;
    case 'select': return <SelectControl entity={entity} onChanged={onChanged} />;
    case 'time': return <TimeControl entity={entity} onChanged={onChanged} />;
    case 'update': return <UpdateControl entity={entity} onChanged={onChanged} />;
    case 'image': return <ImageTile entity={entity} />;
    case 'camera': return <CameraTile entity={entity} />;
    case 'binary_sensor':
      return (
        <Row entity={entity} detail={binaryLabel(entity)}>
          {['door', 'garage_door', 'opening', 'window'].includes(entity.device_class ?? '')
            && <DoorOpen size={16} className={isActive(entity) ? 'text-orange-400' : 'text-slate-600'} aria-hidden />}
        </Row>
      );
    default: return <SensorChip entity={entity} />;
  }
}
