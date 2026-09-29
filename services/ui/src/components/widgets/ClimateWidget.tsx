import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import {
  AlertTriangle, Check, Fan, Minus, Plus, Settings2, Thermometer, Wind, X,
} from 'lucide-react';
import { api } from '../../services/api';
import type {
  ClimateConfig, ClimateLayout, DeviceEntry, IWidgetProps,
} from '../../types/widget';
import {
  actionTone, climateAttrs, clamp, entityLabel, formatTemp, isClimateEntity,
  LAYOUT_OPTIONS, modeVisual, modesFor, numberAttr, OPTIMISTIC_TTL_MS, POLL_MS,
  setpointRange, snap, statusLine, titleize, SETPOINT_DEBOUNCE_MS,
} from '../../lib/climate';
import { useWidgetStore } from '../../stores/widgetStore';
import { useHaptics } from '../../hooks/useHaptics';
import { WidgetCard } from './WidgetCard';

/**
 * Climate — a Nest-style thermostat widget for one or many Home Assistant
 * climate entities.
 *
 * Layouts (Google Home / Material 3 Expressive pattern):
 *  - dial  : 270° ring, arc coloured by the live hvac_action (cyan cooling,
 *            amber heating, slate idle), target temp as the hero number with
 *            touch-friendly steppers. Dual setpoint renders a heat/cool band.
 *  - tiles : one compact ring per room per tile; tap a tile to focus the dial.
 *  - auto  : dial when a single device is shown, tiles when several are.
 *
 * Every control goes through the existing `/execute/ha_service` proxy, which is
 * itself gated by the per-user entity permission check (device assignment, plus
 * any admin-set entity protection). The listing this widget draws from is
 * filtered by that same server-side check, so an entity the user may not touch
 * never appears here at all — there is no second, client-side permission rule
 * that could drift away from the backend's.
 */

const RADIUS = 80;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
const ARC_SWEEP = 0.75; // 270° visible, gap centred at the bottom
const ARC_LENGTH = CIRCUMFERENCE * ARC_SWEEP;

type DialProps = {
  /** Arc fill start/end as 0..1 fractions of the 270° sweep. */
  from: number;
  to: number;
  stroke: string;
  glow: string;
  size: 'compact' | 'regular';
  children: ReactNode;
};

/** The 270° ring. viewBox-driven so it scales inside a 280px phone column. */
function DialArc({ from, to, stroke, glow, size, children }: DialProps) {
  const start = clamp(Math.min(from, to), 0, 1);
  const end = clamp(Math.max(from, to), 0, 1);
  const length = Math.max(0, (end - start) * ARC_LENGTH);
  return (
    <div className={`relative mx-auto aspect-square w-full ${size === 'compact' ? 'max-w-[132px]' : 'max-w-[208px]'}`}>
      <svg viewBox="0 0 200 200" className="h-full w-full" aria-hidden="true" data-testid="climate-ring">
        <circle
          cx="100" cy="100" r={RADIUS}
          fill="none" stroke="rgba(148,163,184,0.16)" strokeWidth="10" strokeLinecap="round"
          strokeDasharray={`${ARC_LENGTH} ${CIRCUMFERENCE}`}
          transform="rotate(135 100 100)"
        />
        {length > 0 && (
          <circle
            cx="100" cy="100" r={RADIUS}
            fill="none" stroke={stroke} strokeWidth="10" strokeLinecap="round"
            strokeDasharray={`${length} ${CIRCUMFERENCE}`}
            strokeDashoffset={-start * ARC_LENGTH}
            transform="rotate(135 100 100)"
            style={{
              transition: 'stroke-dasharray 420ms ease, stroke-dashoffset 420ms ease, stroke 420ms ease',
              filter: glow === 'transparent' ? undefined : `drop-shadow(0 0 10px ${glow})`,
            }}
          />
        )}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">{children}</div>
    </div>
  );
}

/** − / + pair. 44px hit area on touch without inflating the desktop layout. */
function Stepper({
  direction, disabled, onPress, label,
}: { direction: 'down' | 'up'; disabled: boolean; onPress: () => void; label: string }) {
  const Icon = direction === 'down' ? Minus : Plus;
  return (
    <button
      type="button"
      onClick={onPress}
      disabled={disabled}
      aria-label={label}
      className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-white/10 bg-white/5 text-slate-200 transition-all hover:bg-white/10 hover:text-white active:scale-95 disabled:cursor-not-allowed disabled:opacity-35 pointer-coarse:h-11 pointer-coarse:w-11"
    >
      <Icon size={16} strokeWidth={2.5} />
    </button>
  );
}


export type ClimateWidgetProps = IWidgetProps;

type SetpointPatch = { target?: number; low?: number; high?: number };

export function ClimateWidget({ settingsButton, userSettings }: ClimateWidgetProps) {
  const { trigger: haptic } = useHaptics();
  const updateWidgetConfig = useWidgetStore((s) => s.updateWidgetConfig);
  const config = useMemo(
    () => (userSettings.config ?? {}) as ClimateConfig,
    [userSettings.config]
  );
  const size = userSettings.size;

  const [focusId, setFocusId] = useState<string | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [inFlight, setInFlight] = useState<Record<string, boolean>>({});
  const [optimistic, setOptimistic] = useState<Record<string, SetpointPatch>>({});

  // Mirrors `optimistic` so debounced flushes never read a stale closure.
  const pendingRef = useRef<Record<string, SetpointPatch>>({});
  const debounceTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const ttlTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  useEffect(() => () => {
    Object.values(debounceTimers.current).forEach(clearTimeout);
    Object.values(ttlTimers.current).forEach(clearTimeout);
  }, []);

  const { data, isPending, isError, error, refetch } = useQuery({
    queryKey: ['widget', 'climate', 'states'],
    queryFn: () => api.getDeviceStates(['climate']),
    refetchInterval: POLL_MS,
    refetchOnWindowFocus: true,
  });

  const all = useMemo(() => data || [], [data]);

  // The entity list is already filtered server-side: `/execute/entity/search`
  // drops anything this user may neither see nor control, honouring both the
  // device assignment and any entity protection. So presence in `all` *is* the
  // permission answer. This used to be re-derived here from `getDevices()`,
  // which was wrong in two directions: it hid entities an admin had explicitly
  // permitted (a protection permit with no assignment row), and it re-implemented
  // a security rule in the browser where a stale copy silently disagrees with
  // the server. The backend is the single authority, so the client stops guessing.
  const accessible = useMemo(() => new Set(all.map((d) => d.entity_id)), [all]);

  const configured = useMemo(() => {
    const list = config?.devices;
    return Array.isArray(list) ? list.filter((id): id is string => typeof id === 'string') : [];
  }, [config]);
  const layout: ClimateLayout = config?.layout ?? 'auto';

  const devices = useMemo(
    () => configured.map((id) => all.find((d) => d.entity_id === id)).filter((d): d is DeviceEntry => Boolean(d)),
    [configured, all]
  );
  // A configured id absent from the server-filtered list is counted, never
  // listed: it may be one the user can no longer see, and printing the id back
  // would hand over exactly the information the backend withheld.
  const missingCount = useMemo(
    () => configured.filter((id) => !all.some((d) => d.entity_id === id)).length,
    [configured, all]
  );

  const focus = useMemo(() => {
    if (!devices.length) return null;
    return devices.find((d) => d.entity_id === focusId) || devices[0];
  }, [devices, focusId]);

  // A `small` cell can only ever hold the compact dial, so tiles need room.
  const showTiles = layout === 'tiles'
    || (layout === 'auto' && devices.length > 1 && size !== 'small');

  // Drop an optimistic setpoint as soon as Home Assistant echoes it. Derived
  // during render rather than synced in an effect, so a settled value can never
  // be painted for a frame.
  const pending = useMemo(() => {
    const entries = Object.entries(optimistic);
    if (!entries.length) return optimistic;
    const next: Record<string, SetpointPatch> = {};
    for (const [entityId, patch] of entries) {
      const attrs = climateAttrs(devices.find((d) => d.entity_id === entityId));
      const settled = (patch.target === undefined || patch.target === numberAttr(attrs.temperature))
        && (patch.low === undefined || patch.low === numberAttr(attrs.target_temp_low))
        && (patch.high === undefined || patch.high === numberAttr(attrs.target_temp_high));
      if (!settled) next[entityId] = patch;
    }
    return next;
  }, [optimistic, devices]);

  const run = useCallback(async (
    entityId: string,
    service: string,
    payload: Record<string, unknown>,
    busyKey: string
  ) => {
    if (!accessible.has(entityId)) {
      // The entity dropped out of the server-filtered list — a lock was added,
      // or the assignment was removed. Say so rather than firing a doomed call.
      toast.error(`Access Denied: ${entityId} is no longer available to your account.`);
      return false;
    }
    setInFlight((prev) => ({ ...prev, [busyKey]: true }));
    try {
      await api.callHaService('climate', service, entityId, payload);
      void haptic('light');
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `climate.${service} failed`);
      return false;
    } finally {
      setInFlight((prev) => {
        const next = { ...prev };
        delete next[busyKey];
        return next;
      });
    }
  }, [accessible, haptic]);

  /**
   * Apply a setpoint change: paint it immediately, then send one debounced
   * `climate.set_temperature`. `heat_cool` entities need low and high together.
   */
  const queueSetpoint = useCallback((device: DeviceEntry, patch: SetpointPatch) => {
    const entityId = device.entity_id;
    const merged = { ...(pendingRef.current[entityId] || {}), ...patch };
    pendingRef.current[entityId] = merged;
    setOptimistic((prev) => ({ ...prev, [entityId]: merged }));
    void haptic('light');

    clearTimeout(debounceTimers.current[entityId]);
    clearTimeout(ttlTimers.current[entityId]);
    // If Home Assistant never echoes the value, stop lying about it.
    ttlTimers.current[entityId] = setTimeout(() => {
      delete pendingRef.current[entityId];
      setOptimistic((prev) => {
        const next = { ...prev };
        delete next[entityId];
        return next;
      });
    }, OPTIMISTIC_TTL_MS);

    debounceTimers.current[entityId] = setTimeout(() => {
      const pending = pendingRef.current[entityId] || {};
      const attrs = climateAttrs(device);
      const dual = typeof attrs.target_temp_low === 'number' && typeof attrs.target_temp_high === 'number';
      const payload = dual
        ? {
            target_temp_low: pending.low ?? numberAttr(attrs.target_temp_low),
            target_temp_high: pending.high ?? numberAttr(attrs.target_temp_high),
          }
        : { temperature: pending.target ?? numberAttr(attrs.temperature) };
      void run(entityId, 'set_temperature', payload, `${entityId}:set_temperature`);
    }, SETPOINT_DEBOUNCE_MS);
  }, [haptic, run]);

  const stepSetpoint = useCallback((device: DeviceEntry, kind: 'target' | 'low' | 'high', direction: 1 | -1) => {
    const attrs = climateAttrs(device);
    const { min, max, step } = setpointRange(attrs);
    const pending = pendingRef.current[device.entity_id] || {};
    const live = kind === 'target'
      ? numberAttr(attrs.temperature)
      : kind === 'low'
        ? numberAttr(attrs.target_temp_low)
        : numberAttr(attrs.target_temp_high);
    const current = pending[kind] ?? live;
    if (typeof current !== 'number') {
      toast.error(`${device.entity_id} reports no target temperature to adjust.`);
      return;
    }
    const next = snap(current + direction * step, min, max, step);
    if (next === current) return;
    if (kind === 'low') {
      const high = pending.high ?? numberAttr(attrs.target_temp_high);
      queueSetpoint(device, typeof high === 'number' ? { low: Math.min(next, high) } : { low: next });
    } else if (kind === 'high') {
      const low = pending.low ?? numberAttr(attrs.target_temp_low);
      queueSetpoint(device, typeof low === 'number' ? { high: Math.max(next, low) } : { high: next });
    } else {
      queueSetpoint(device, { target: next });
    }
  }, [queueSetpoint]);

  const setMode = useCallback((device: DeviceEntry, mode: string) => {
    const current = climateAttrs(device).hvac_mode;
    if (current === mode) return;
    void run(device.entity_id, 'set_hvac_mode', { hvac_mode: mode }, `${device.entity_id}:mode`);
  }, [run]);

  const setPreset = useCallback((device: DeviceEntry, preset: string) => {
    void run(device.entity_id, 'set_preset_mode', { preset_mode: preset }, `${device.entity_id}:preset`);
  }, [run]);

  const setFanMode = useCallback((device: DeviceEntry, mode: string) => {
    void run(device.entity_id, 'set_fan_mode', { fan_mode: mode }, `${device.entity_id}:fan`);
  }, [run]);

  const setSwingMode = useCallback((device: DeviceEntry, mode: string) => {
    void run(device.entity_id, 'set_swing_mode', { swing_mode: mode }, `${device.entity_id}:swing`);
  }, [run]);

  const saveSetup = useCallback(async (deviceIds: string[], nextLayout: ClimateLayout) => {
    const widgetKey = userSettings.widget_key;
    if (!widgetKey) {
      toast.error('This climate widget has no widget key, so its device list cannot be saved.');
      return;
    }
    setSaving(true);
    try {
      await updateWidgetConfig(widgetKey, { devices: deviceIds, layout: nextLayout });
      toast.success(deviceIds.length
        ? `Tracking ${deviceIds.length} climate device${deviceIds.length === 1 ? '' : 's'}`
        : 'No climate devices selected');
      setSetupOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save climate devices');
    } finally {
      setSaving(false);
    }
  }, [updateWidgetConfig, userSettings.widget_key]);

  const deviceName = (device: DeviceEntry) => entityLabel(device);

  const controlsDisabled = (device: DeviceEntry) =>
    !accessible.has(device.entity_id) || device.state === 'unavailable' || device.state === 'unknown';

  /** Hero dial for one device: ring + setpoint control + modes + presets. */
  const renderDial = (device: DeviceEntry) => {
    const attrs = climateAttrs(device);
    const patch = pending[device.entity_id] || {};
    const { min, max } = setpointRange(attrs);
    const tone = actionTone(attrs.hvac_action, attrs.hvac_mode);
    const current = numberAttr(attrs.current_temperature);
    const target = patch.target ?? numberAttr(attrs.temperature);
    const low = patch.low ?? numberAttr(attrs.target_temp_low);
    const high = patch.high ?? numberAttr(attrs.target_temp_high);
    const dual = typeof low === 'number' && typeof high === 'number' && attrs.hvac_mode === 'heat_cool';
    const off = device.state === 'off';
    const span = Math.max(max - min, 1);
    const frac = (value: number) => clamp((value - min) / span, 0, 1);
    const disabled = controlsDisabled(device);
    const compact = size === 'small';

    const from = off || typeof target !== 'number' ? 0 : dual ? frac(low) : 0;
    const to = off
      ? 0
      : dual
        ? (typeof high === 'number' ? frac(high) : 0)
        : typeof target === 'number' ? frac(target) : 0;

    return (
      <div className="flex h-full min-h-0 flex-col gap-1.5" data-testid="climate-dial" data-entity={device.entity_id}>
        <div className="flex items-center gap-1">
          {!dual && (
            <Stepper
              direction="down"
              disabled={disabled || typeof target !== 'number'}
              onPress={() => stepSetpoint(device, 'target', -1)}
              label={`Lower target for ${deviceName(device)}`}
            />
          )}
          <div className="min-w-0 flex-1">
            <DialArc from={from} to={to} stroke={tone.stroke} glow={tone.glow} size={compact ? 'compact' : 'regular'}>
              <span className={`tabular-nums leading-none font-bold ${compact ? 'text-3xl' : 'text-4xl'} ${off ? 'text-slate-300' : tone.text}`}>
                {formatTemp(off ? current : (dual ? current : target))}
                <span className="align-top text-base font-semibold">°</span>
              </span>
              <span className="mt-1 text-[10px] font-medium text-slate-400">
                {off ? 'System off' : (dual ? `${formatTemp(current)}° now` : `of ${formatTemp(target)}°`)}
              </span>
            </DialArc>
          </div>
          {!dual && (
            <Stepper
              direction="up"
              disabled={disabled || typeof target !== 'number'}
              onPress={() => stepSetpoint(device, 'target', 1)}
              label={`Raise target for ${deviceName(device)}`}
            />
          )}
        </div>

        <p className={`truncate text-center text-[10px] font-semibold uppercase tracking-wider ${tone.text}`}>
          {statusLine(device, attrs)}
        </p>

        {dual && (
          <div className="grid grid-cols-2 gap-1.5">
            {([['low', 'Heat', 'text-amber-300'], ['high', 'Cool', 'text-cyan-300']] as const).map(([kind, label, tint]) => (
              <div key={kind} className="flex items-center justify-between rounded-xl border border-white/5 bg-white/[0.03] px-1.5 py-1">
                <span className={`text-[9px] font-black uppercase tracking-wider ${tint}`}>{label}</span>
                <div className="flex items-center gap-0.5">
                  <Stepper
                    direction="down"
                    disabled={disabled}
                    onPress={() => stepSetpoint(device, kind, -1)}
                    label={`Lower ${label.toLowerCase()} setpoint for ${deviceName(device)}`}
                  />
                  <span className="w-9 text-center text-xs font-bold text-white tabular-nums">
                    {formatTemp(kind === 'low' ? low : high)}°
                  </span>
                  <Stepper
                    direction="up"
                    disabled={disabled}
                    onPress={() => stepSetpoint(device, kind, 1)}
                    label={`Raise ${label.toLowerCase()} setpoint for ${deviceName(device)}`}
                  />
                </div>
              </div>
            ))}
          </div>
        )}

        {!compact && (
          <div className="flex gap-1 overflow-x-auto pb-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {modesFor(device).map((mode) => {
              const { Icon, label, tone: modeTone } = modeVisual(mode);
              const active = attrs.hvac_mode === mode;
              return (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setMode(device, mode)}
                  disabled={disabled || inFlight[`${device.entity_id}:mode`] === true}
                  aria-pressed={active}
                  title={label}
                  className={`flex shrink-0 items-center gap-1 rounded-lg border px-2 text-[10px] font-bold transition-colors pointer-coarse:min-h-11 ${
                    active
                      ? 'border-white/15 bg-white/10 text-white'
                      : 'border-transparent bg-white/[0.03] text-slate-400 hover:bg-white/[0.07] hover:text-slate-200'
                  }`}
                >
                  <Icon size={12} className={active ? modeTone : undefined} />
                  {label}
                </button>
              );
            })}
          </div>
        )}

        {Array.isArray(attrs.preset_modes) && attrs.preset_modes.length > 0 && (
          <div className="flex gap-1 overflow-x-auto pb-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {attrs.preset_modes.map((preset) => {
              const active = attrs.preset_mode === preset;
              return (
                <button
                  key={preset}
                  type="button"
                  onClick={() => setPreset(device, preset)}
                  disabled={disabled}
                  aria-pressed={active}
                  className={`shrink-0 rounded-full border px-2 py-1 text-[10px] font-semibold transition-colors pointer-coarse:min-h-11 ${
                    active
                      ? 'border-violet-400/40 bg-violet-400/15 text-violet-200'
                      : 'border-white/5 bg-white/[0.03] text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {titleize(preset)}
                </button>
              );
            })}
          </div>
        )}

        <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-0.5 text-[10px] text-slate-400">
          {typeof current === 'number' && (
            <span className="tabular-nums">Now {formatTemp(current)}°</span>
          )}
          {typeof attrs.humidity === 'number' && <span className="tabular-nums">Humidity {Math.round(attrs.humidity)}%</span>}
          {Array.isArray(attrs.fan_modes) && attrs.fan_modes.length > 0 && (
            <label className="flex items-center gap-1">
              <span className="sr-only">Fan mode for {deviceName(device)}</span>
              <Fan size={11} className="text-teal-300" aria-hidden="true" />
              <select
                value={attrs.fan_mode || ''}
                disabled={disabled}
                onChange={(e) => setFanMode(device, e.target.value)}
                className="glass-input h-8 min-w-0 max-w-[110px] rounded-md bg-white/5 px-1 text-[10px] text-slate-200 pointer-coarse:h-11"
              >
                {attrs.fan_modes.map((mode) => <option key={mode} value={mode}>{titleize(mode)}</option>)}
              </select>
            </label>
          )}
          {Array.isArray(attrs.swing_modes) && attrs.swing_modes.length > 0 && (
            <label className="flex items-center gap-1">
              <span className="sr-only">Swing mode for {deviceName(device)}</span>
              <Wind size={11} className="text-sky-300" aria-hidden="true" />
              <select
                value={attrs.swing_mode || ''}
                disabled={disabled}
                onChange={(e) => setSwingMode(device, e.target.value)}
                className="glass-input h-8 min-w-0 max-w-[110px] rounded-md bg-white/5 px-1 text-[10px] text-slate-200 pointer-coarse:h-11"
              >
                {attrs.swing_modes.map((mode) => <option key={mode} value={mode}>{titleize(mode)}</option>)}
              </select>
            </label>
          )}
        </div>
      </div>
    );
  };


  /** Compact room tile. The whole tile is the tap target → promotes to dial. */
  const renderTile = (device: DeviceEntry) => {
    const attrs = climateAttrs(device);
    const tone = actionTone(attrs.hvac_action, attrs.hvac_mode);
    const target = pending[device.entity_id]?.target ?? numberAttr(attrs.temperature);
    const current = numberAttr(attrs.current_temperature);
    const { min, max } = setpointRange(attrs);
    const span = Math.max(max - min, 1);
    const off = device.state === 'off';
    const { Icon } = modeVisual(attrs.hvac_mode);
    const fill = typeof target === 'number' ? clamp((target - min) / span, 0, 1) : 0;
    return (
      <button
        key={device.entity_id}
        type="button"
        onClick={() => { setFocusId(device.entity_id); void haptic('light'); }}
        data-testid="climate-tile"
        data-entity={device.entity_id}
        className="flex min-h-[92px] flex-col items-center justify-center gap-1 rounded-2xl border border-white/5 bg-white/[0.03] p-2 text-center transition-all hover:bg-white/[0.07] active:scale-[0.98]"
      >
        <span className="flex w-full items-center gap-1 text-[10px] font-semibold text-slate-400">
          <Icon size={11} className={`shrink-0 ${tone.text}`} aria-hidden="true" />
          <span className="truncate">{deviceName(device)}</span>
        </span>
        <span className={`tabular-nums text-xl font-bold leading-none ${off ? 'text-slate-400' : tone.text}`}>
          {formatTemp(off ? current : target)}°
        </span>
        <span className="text-[9px] tabular-nums text-slate-500">
          {typeof current === 'number' ? `${formatTemp(current)}° now` : statusLine(device, attrs)}
        </span>
        <span className="mt-0.5 block h-1 w-full overflow-hidden rounded-full bg-white/10">
          <span
            className="block h-full rounded-full transition-[width] duration-500"
            style={{ width: `${off ? 0 : fill * 100}%`, background: tone.stroke }}
          />
        </span>
      </button>
    );
  };

  const viewingDial = !showTiles || focusId !== null;
  const configureButton = (
    <button
      type="button"
      onClick={() => setSetupOpen(true)}
      className="glass-button px-2 py-1 text-[10px] text-slate-300 hover:text-white pointer-coarse:min-h-11"
    >
      <Settings2 size={11} />
      Devices
    </button>
  );


  const body = () => {
    if (configured.length === 0) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-2 p-3 text-center">
          <Thermometer size={24} className="text-slate-700" />
          <p className="text-xs font-semibold text-slate-300">No climate devices selected</p>
          <p className="text-[10px] leading-snug text-slate-500">
            Pick the <span className="font-mono">climate.*</span> entities this widget should control.
          </p>
          {configureButton}
        </div>
      );
    }
    if (devices.length === 0) {
      // See the note on `missingCount`: a missing id may be one the user can no
      // longer see, so it is a count and never a list.
      return (
        <div className="flex h-full flex-col items-center justify-center gap-2 p-3 text-center">
          <AlertTriangle size={22} className="text-amber-400" />
          <p className="text-xs font-semibold text-amber-300">
            {missingCount === 1 ? 'Configured device unavailable' : 'Configured devices unavailable'}
          </p>
          <p className="text-[10px] leading-snug text-slate-500">
            They may have been removed, renamed, or made unavailable to your account.
          </p>
          <div className="flex gap-1.5">
            <button type="button" onClick={() => void refetch()} className="glass-button px-2 py-1 text-[10px] text-slate-300 pointer-coarse:min-h-11">
              Retry
            </button>
            {configureButton}
          </div>
        </div>
      );
    }
    return (
      <div className="flex h-full min-h-0 flex-col gap-1.5">
        {devices.length > 1 && (
          <div className="flex gap-1 overflow-x-auto pb-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {viewingDial && devices.map((d) => (
              <button
                key={d.entity_id}
                type="button"
                onClick={() => setFocusId(d.entity_id)}
                aria-pressed={focus?.entity_id === d.entity_id}
                className={`shrink-0 rounded-lg px-2 py-1 text-[10px] font-bold transition-colors pointer-coarse:min-h-11 ${
                  focus?.entity_id === d.entity_id
                    ? 'bg-white/10 text-white'
                    : 'bg-white/[0.03] text-slate-400 hover:text-slate-200'
                }`}
              >
                {deviceName(d)}
              </button>
            ))}
            {viewingDial && layout !== 'dial' && (
              <button
                type="button"
                onClick={() => setFocusId(null)}
                className="ml-auto shrink-0 rounded-lg bg-white/[0.03] px-2 py-1 text-[10px] font-bold text-slate-400 transition-colors hover:text-slate-200 pointer-coarse:min-h-11"
              >
                All rooms
              </button>
            )}
            {!viewingDial && (
              <span className="shrink-0 self-center text-[10px] font-bold uppercase tracking-wider text-slate-600">
                {devices.length} rooms · tap to control
              </span>
            )}
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {viewingDial && focus
            ? renderDial(focus)
            : <div className="grid grid-cols-2 gap-1.5">{devices.map(renderTile)}</div>}
        </div>
      </div>
    );
  }

  const focusAttrs = focus ? climateAttrs(focus) : undefined;

  return (
    <>
      <WidgetCard
        title={devices.length > 1 ? 'Climate' : (focus ? deviceName(focus) : 'Climate')}
        isLoading={isPending && devices.length === 0}
        error={isError ? (error instanceof Error ? error.message : 'Climate states unavailable') : null}
        onRetry={() => void refetch()}
        settingsButton={settingsButton}
        accentColor={focusAttrs ? actionTone(focusAttrs.hvac_action, focusAttrs.hvac_mode).stroke : undefined}
        actions={devices.length > 1 ? configureButton : undefined}
      >
        {body()}
      </WidgetCard>
      {setupOpen && (
        <DeviceSetupDialog
          entities={all}
          selected={configured}
          layout={layout}
          saving={saving}
          onCancel={() => setSetupOpen(false)}
          onSave={saveSetup}
        />
      )}
    </>
  );
}


interface DeviceSetupDialogProps {
  entities: DeviceEntry[];
  selected: string[];
  layout: ClimateLayout;
  saving: boolean;
  onCancel: () => void;
  onSave: (deviceIds: string[], layout: ClimateLayout) => void;
}

/**
 * Device picker + layout chooser. Opens as a bottom sheet on phones and a
 * centred dialog from `sm` up, and lists every climate entity Home Assistant
 * reported — nothing is pre-selected or hard-coded.
 */
export function DeviceSetupDialog({
  entities, selected, layout, saving, onCancel, onSave,
}: DeviceSetupDialogProps) {
  const [draft, setDraft] = useState<string[]>(selected);
  const [draftLayout, setDraftLayout] = useState<ClimateLayout>(layout);
  const [query, setQuery] = useState('');

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);

  const climateEntities = useMemo(
    () => entities.filter(isClimateEntity),
    [entities]
  );
  const shown = useMemo(() => {
    const term = query.trim().toLowerCase();
    return climateEntities
      .filter((e) => !term || e.entity_id.toLowerCase().includes(term) || entityLabel(e).toLowerCase().includes(term))
      .sort((a, b) => entityLabel(a).localeCompare(entityLabel(b)));
  }, [climateEntities, query]);

  const toggle = (entityId: string) => {
    setDraft((prev) => (prev.includes(entityId) ? prev.filter((id) => id !== entityId) : [...prev, entityId]));
  };

  return (
    <div
      className="fixed inset-0 z-[70] flex items-end justify-center bg-black/70 p-0 backdrop-blur-sm sm:items-center sm:p-4"
      onClick={onCancel}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Climate widget devices"
        onClick={(e) => e.stopPropagation()}
        className="glass-panel flex max-h-[85vh] w-full flex-col overflow-hidden rounded-t-2xl sm:max-w-md sm:rounded-2xl safe-area-bottom"
      >
        <header className="flex items-center justify-between gap-2 border-b border-white/5 px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-sm font-bold text-white">Climate devices</h2>
            <p className="text-[10px] text-slate-500">{draft.length} selected · {climateEntities.length} available</p>
          </div>
          <button
            type="button"
            onClick={onCancel}
            aria-label="Close"
            className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-400 hover:bg-white/5 hover:text-white pointer-coarse:h-11 pointer-coarse:w-11"
          >
            <X size={16} />
          </button>
        </header>


        <div className="border-b border-white/5 px-4 py-2.5">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter entities…"
            aria-label="Filter climate entities"
            className="glass-input h-9 w-full rounded-lg bg-white/5 px-2.5 text-xs text-white pointer-coarse:h-11"
          />
          <div className="mt-2 grid grid-cols-3 gap-1" role="radiogroup" aria-label="Widget layout">
            {LAYOUT_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={draftLayout === option.value}
                title={option.hint}
                onClick={() => setDraftLayout(option.value)}
                className={`rounded-lg border px-1.5 py-1.5 text-[10px] font-bold transition-colors pointer-coarse:min-h-11 ${
                  draftLayout === option.value
                    ? 'border-white/15 bg-white/10 text-white'
                    : 'border-white/5 bg-white/[0.02] text-slate-400 hover:text-slate-200'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1.5">
          {climateEntities.length === 0 ? (
            <p className="p-4 text-center text-[11px] leading-snug text-slate-500">
              Home Assistant returned no <span className="font-mono">climate</span> entities, so there is nothing to
              select. Check the Home Assistant integration and connection settings.
            </p>
          ) : shown.length === 0 ? (
            <p className="p-4 text-center text-[11px] text-slate-500">No climate entity matches “{query}”.</p>
          ) : shown.map((entity) => {
            const active = draft.includes(entity.entity_id);
            const attrs = climateAttrs(entity);
            return (
              <button
                key={entity.entity_id}
                type="button"
                role="checkbox"
                aria-checked={active}
                onClick={() => toggle(entity.entity_id)}
                className="flex w-full items-center gap-2.5 rounded-xl px-2 py-2 text-left transition-colors hover:bg-white/5 pointer-coarse:min-h-11"
              >
                <span
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md border ${
                    active ? 'border-violet-400/60 bg-violet-400/25 text-violet-100' : 'border-white/15'
                  }`}
                >
                  {active && <Check size={12} />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-semibold text-white">{entityLabel(entity)}</span>
                  <span className="block truncate font-mono text-[10px] text-slate-500">{entity.entity_id}</span>
                </span>
                <span className="shrink-0 text-right text-[10px] tabular-nums text-slate-400">
                  {typeof attrs.current_temperature === 'number' ? `${Math.round(attrs.current_temperature)}°` : entity.state}
                </span>
              </button>
            );
          })}
        </div>

        <footer className="flex gap-2 border-t border-white/5 px-4 py-3">
          <button type="button" onClick={onCancel} className="glass-button flex-1 px-3 py-2 text-xs text-slate-300 pointer-coarse:min-h-11">
            Cancel
          </button>
          <button
            type="button"
            disabled={saving}
            onClick={() => onSave(draft, draftLayout)}
            className="glass-button flex-1 px-3 py-2 text-xs font-bold text-white disabled:opacity-50 pointer-coarse:min-h-11"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </footer>
      </div>
    </div>
  );
}

export default ClimateWidget;