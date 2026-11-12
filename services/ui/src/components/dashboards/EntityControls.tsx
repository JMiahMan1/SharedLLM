import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  CameraOff, Cctv, ChevronDown, ChevronUp, DoorOpen, Lightbulb, Lock, LockOpen, Minus, Pause, Play, Plus, Power,
  SkipForward, Volume2,
} from 'lucide-react';
import { useMediaToken } from '../../lib/mediaToken';
import { cameraMediaUrl } from '../../lib/cameras';
import {
  SETPOINT_DEBOUNCE_MS, modeVisual, numberAttr, setpointRange, snap, titleize, type ClimateAttributes,
} from '../../lib/climate';
import {
  binaryLabel, brightnessPct, formatState, isActive, isUnavailable, runService, supportsBrightness, type HubEntity,
} from '../../lib/dashboards';

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
      <Row entity={shown} detail={isUnavailable(shown.state) ? 'Unavailable' : on && pct != null ? `${pct}%` : on ? 'On' : 'Off'}>
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
        {off ? (
          <button type="button" aria-label={`Turn on ${entity.name}`} className={`${TOUCH} rounded-lg bg-white/5`}
            disabled={isUnavailable(shown.state)} onClick={() => act('media_player', 'turn_on')}>
            <Power size={16} className="mx-auto text-slate-300" />
          </button>
        ) : (
          <>
            <button type="button" aria-label={shown.state === 'playing' ? `Pause ${entity.name}` : `Play ${entity.name}`}
              className={`${TOUCH} rounded-lg bg-white/5`}
              onClick={() => act('media_player', 'media_play_pause', undefined,
                { state: shown.state === 'playing' ? 'paused' : 'playing' })}>
              {shown.state === 'playing' ? <Pause size={16} className="mx-auto" /> : <Play size={16} className="mx-auto" />}
            </button>
            <button type="button" aria-label={`Next on ${entity.name}`} className={`${TOUCH} rounded-lg bg-white/5`}
              onClick={() => act('media_player', 'media_next_track')}>
              <SkipForward size={16} className="mx-auto" />
            </button>
          </>
        )}
      </Row>
      {!off && vol != null && (
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
    controls = <>{btn('Open', 'open_cover', <ChevronUp size={16} className="mx-auto" />, 'opening')}
      {btn('Close', 'close_cover', <ChevronDown size={16} className="mx-auto" />, 'closing')}</>;
  } else if (entity.domain === 'lock') {
    controls = shown.state === 'locked'
      ? btn('Unlock', 'unlock', <LockOpen size={16} className="mx-auto" />, 'unlocking')
      : btn('Lock', 'lock', <Lock size={16} className="mx-auto" />, 'locking');
  } else if (entity.domain === 'vacuum') {
    controls = <>{shown.state === 'cleaning'
      ? btn('Pause', 'pause', <Pause size={16} className="mx-auto" />, 'paused')
      : btn('Start', 'start', <Play size={16} className="mx-auto" />, 'cleaning')}
      {btn('Dock', 'return_to_base', <span className="text-xs">Dock</span>, 'returning')}</>;
  }
  const battery = numberAttr(shown.attributes?.battery_level);
  return (
    <Row entity={shown} detail={`${titleize(shown.state)}${battery != null ? ` · ${battery}%` : ''}`}>{controls}</Row>
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
            {unavailable ? 'Unavailable' : [
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
  return (
    <Link to={`/cameras?camera=${encodeURIComponent(entity.entity_id)}`}
      className="block overflow-hidden rounded-xl border border-white/10 bg-black/40" aria-label={`Open ${entity.name}`}>
      <div className="aspect-video flex items-center justify-center">
        {url && !failed ? (
          <img src={url} alt={entity.name} loading="lazy" className="h-full w-full object-cover" onError={() => setFailed(true)} />
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
    case 'remote':
      return (
        <Row entity={entity} detail={titleize(entity.state)}>
          <Link to="/remote" className={`${TOUCH} flex items-center rounded-lg bg-white/5 px-3 text-xs text-slate-200`}>Remote</Link>
        </Row>
      );
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
