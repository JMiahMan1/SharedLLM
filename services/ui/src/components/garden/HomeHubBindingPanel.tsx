import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { toast } from 'react-hot-toast';
import { Droplets, Play, Square, Thermometer, Sun, Wind } from 'lucide-react';
import { api } from '../../services/api';
import type {
  GardenBindingIn,
  GardenHardwareDiscovery,
  GardenHomeHubBinding,
  GardenHubEntity,
} from '../../types/api';

const cardClass = 'rounded-xl border border-white/10 bg-white/5 p-4';
const labelClass = 'flex flex-col gap-1 text-[10px] text-slate-400';
const inputClass =
  'w-full px-3 py-2 min-h-11 pointer-coarse:min-h-11 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-emerald-500 text-sm';
const buttonClass =
  'inline-flex items-center justify-center gap-1.5 px-3 py-2 min-h-11 rounded-lg border text-xs transition-colors disabled:opacity-50';

/** Does this house have any garden hardware worth showing? */
export function useGardenHardware() {
  const query = useQuery({
    queryKey: ['garden-hardware'],
    queryFn: () => api.gardenHardwareDiscover(),
    staleTime: 60_000,
  });
  return query;
}

function ReadingChip({
  icon, label, value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-white/5 border border-white/10 text-[11px] text-slate-300">
      {icon}
      <span className="text-slate-400">{label}</span>
      <span className="text-white font-medium">{value}</span>
    </span>
  );
}

/** The live numbers a bound sensor gives us; nothing renders when unbound. */
export function BindingReadings({ binding }: { binding: GardenHomeHubBinding }) {
  const readings = binding.readings || {};
  const chips: React.ReactNode[] = [];
  if (typeof readings.moisture_pct === 'number') {
    chips.push(<ReadingChip key="m" icon={<Droplets size={12} />} label="Soil" value={`${readings.moisture_pct}%`} />);
  }
  if (typeof readings.temperature === 'number') {
    chips.push(
      <ReadingChip key="t" icon={<Thermometer size={12} />} label="Temp" value={`${readings.temperature}${readings.temperature_unit || ''}`} />,
    );
  }
  if (typeof readings.humidity_pct === 'number') {
    chips.push(<ReadingChip key="h" icon={<Wind size={12} />} label="Humidity" value={`${readings.humidity_pct}%`} />);
  }
  if (typeof readings.lux === 'number') {
    chips.push(<ReadingChip key="l" icon={<Sun size={12} />} label="Light" value={`${Math.round(readings.lux)} lx`} />);
  }
  if (chips.length === 0) return null;
  return <div className="flex flex-wrap gap-2">{chips}</div>;
}

/** A picker over one class of Home Hub entities. */
function EntityPicker({
  id, label, entities, value, onChange, placeholder = 'Not bound',
}: {
  id: string;
  label: string;
  entities: GardenHubEntity[];
  value: string | null;
  onChange: (next: string | null) => void;
  placeholder?: string;
}) {
  if (entities.length === 0) return null;
  return (
    <label className={labelClass} htmlFor={id}>
      {label}
      <select
        id={id}
        className={inputClass}
        value={value ?? ''}
        onChange={(event) => onChange(event.target.value || null)}
      >
        <option value="">{placeholder}</option>
        {entities.map((entity) => (
          <option key={entity.entity_id} value={entity.entity_id}>
            {entity.friendly_name || entity.entity_id}
          </option>
        ))}
      </select>
    </label>
  );
}

/** The valve controls: a guarded run with its caps in view, and a stop. */
function ValveControls({
  binding, canEdit,
}: {
  binding: GardenHomeHubBinding;
  canEdit: boolean;
}) {
  const queryClient = useQueryClient();
  const [minutes, setMinutes] = useState(String(binding.max_run_minutes));
  const [confirming, setConfirming] = useState(false);

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['garden-bindings'] });
  const [rainBlocked, setRainBlocked] = useState<string | null>(null);
  const run = useMutation({
    mutationFn: (override?: boolean) =>
      api.gardenRunValve(binding.id, {
        minutes: Number(minutes) || undefined,
        ...(override ? { override_rain: true } : {}),
      }),
    onSuccess: (result) => {
      setConfirming(false);
      setRainBlocked(null);
      toast.success(`Valve opened for ${result.minutes} min (${result.runs_today}/${result.daily_max_runs} today)`);
      refresh();
    },
    onError: (error: unknown) => {
      const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      // A rain block is a *choice*, not a failure: offer the deliberate override.
      setRainBlocked(detail && /rain interlock/i.test(detail) ? detail : null);
      toast.error(detail || 'The valve could not be opened');
    },
  });
  const stop = useMutation({
    mutationFn: () => api.gardenStopValve(binding.id),
    onSuccess: () => {
      toast.success('Valve closed');
      refresh();
    },
    onError: () => toast.error('Home Hub did not confirm the valve closed'),
  });

  if (!binding.valve_entity_id) return null;
  const running = Boolean(binding.active_run_started_at);

  return (
    <div className="rounded-lg border border-white/10 bg-slate-950/40 p-3 space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] text-slate-300">
          Irrigation valve · {binding.runs_today} / {binding.daily_max_runs} runs today
          {running ? ' · running now' : ''}
        </p>
        {running && binding.active_run_ends_at && (
          <p className="text-[11px] text-amber-200">Closes {new Date(binding.active_run_ends_at).toLocaleTimeString()}</p>
        )}
      </div>
      {canEdit && (
        <div className="flex flex-wrap items-end gap-2">
          <label className={`${labelClass} w-24`} htmlFor={`valve-minutes-${binding.id}`}>
            Minutes
            <input
              id={`valve-minutes-${binding.id}`}
              type="number"
              min={1}
              max={binding.max_run_minutes}
              className={inputClass}
              value={minutes}
              onChange={(event) => setMinutes(event.target.value)}
            />
          </label>
          <button
            type="button"
            className={`${buttonClass} bg-emerald-500/10 border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/20`}
            onClick={() => (confirming ? run.mutate(false) : setConfirming(true))}
            disabled={run.isPending || running}
          >
            <Play size={12} /> {confirming ? 'Confirm run' : 'Run valve'}
          </button>
          <button
            type="button"
            className={`${buttonClass} bg-rose-500/10 border-rose-500/30 text-rose-300 hover:bg-rose-500/20`}
            onClick={() => stop.mutate()}
            disabled={stop.isPending}
          >
            <Square size={12} /> Stop now
          </button>
        </div>
      )}
      {rainBlocked && canEdit && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 space-y-1">
          <p className="text-[11px] text-amber-200">{rainBlocked}</p>
          <button
            type="button"
            onClick={() => run.mutate(true)}
            disabled={run.isPending}
            className={`${buttonClass} bg-amber-500/15 border-amber-500/40 text-amber-200 hover:bg-amber-500/25`}
          >
            <Play size={12} /> Run anyway (override rain)
          </button>
        </div>
      )}
      {confirming && (
        <p className="text-[11px] text-amber-200">
          Tap again to open for {minutes} min. The valve closes itself; the cap is {binding.max_run_minutes} min.
        </p>
      )}
      {binding.rain_interlock && (
        <p className="text-[10px] text-slate-500">Rain interlock is on: a wet day blocks an uncovered bed.</p>
      )}
    </div>
  );
}

/**
 * A grow-light schedule needs both times, and the inputs show 06:00 / 20:00
 * until they are touched -- so those are what gets saved.
 */
function withScheduleDefaults(draft: GardenBindingIn): GardenBindingIn {
  if (!draft.grow_light_enabled) return draft;
  return {
    ...draft,
    grow_light_on_time: draft.grow_light_on_time ?? '06:00',
    grow_light_off_time: draft.grow_light_off_time ?? '20:00',
  };
}


/**
 * A plant's or bed's Home Hub hardware.
 *
 * Renders **nothing at all** when the house has no garden hardware and this
 * target has no binding -- a home with no sensors must see no trace of them.
 * Everyone sees the live readings; only parents and admins see the controls.
 */
export function HomeHubBindingPanel({
  targetKind, targetId, canEdit,
}: {
  targetKind: 'plant' | 'location';
  targetId: string;
  canEdit: boolean;
}) {
  const hardwareQuery = useGardenHardware();
  const bindingsQuery = useQuery({
    queryKey: ['garden-bindings', targetKind, targetId],
    queryFn: () => api.gardenListBindings(targetKind === 'plant' ? { plant_id: targetId } : { location_id: targetId }),
    staleTime: 30_000,
  });

  const discovery: GardenHardwareDiscovery | undefined = hardwareQuery.data;
  const bindings = bindingsQuery.data?.bindings ?? [];
  const own = bindings.find((row) => row.target_kind === targetKind && row.target_id === targetId);
  const [draft, setDraft] = useState<GardenBindingIn>({});

  useEffect(() => {
    if (!own) return;
    setDraft({
      moisture_entity_id: own.moisture_entity_id,
      temperature_entity_id: own.temperature_entity_id,
      humidity_entity_id: own.humidity_entity_id,
      lux_entity_id: own.lux_entity_id,
      grow_light_entity_id: own.grow_light_entity_id,
      grow_light_on_time: own.grow_light_on_time,
      grow_light_off_time: own.grow_light_off_time,
      grow_light_enabled: own.grow_light_enabled,
      valve_entity_id: own.valve_entity_id,
      valve_mode: own.valve_mode,
      moisture_override_enabled: own.moisture_override_enabled,
      moisture_threshold_pct: own.moisture_threshold_pct,
      max_run_minutes: own.max_run_minutes,
      daily_max_runs: own.daily_max_runs,
      rain_interlock: own.rain_interlock,
      nfc_tag_id: own.nfc_tag_id,
    });
  }, [own]);

  const queryClient = useQueryClient();
  const unbind = useMutation({
    mutationFn: () => api.gardenDeleteBinding(targetKind, targetId),
    onSuccess: () => {
      setDraft({});
      toast.success('Hardware unbound');
      void queryClient.invalidateQueries({ queryKey: ['garden-bindings'] });
      void queryClient.invalidateQueries({ queryKey: ['garden-plants'] });
    },
    onError: () => toast.error('That binding could not be removed'),
  });
  const save = useMutation({
    mutationFn: () => api.gardenSaveBinding(targetKind, targetId, withScheduleDefaults(draft)),
    onSuccess: () => {
      toast.success('Hardware saved');
      void queryClient.invalidateQueries({ queryKey: ['garden-bindings'] });
      void queryClient.invalidateQueries({ queryKey: ['garden-plants'] });
    },
    onError: (error: unknown) => {
      const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || 'That hardware setting was refused');
    },
  });

  // Progressive disclosure: nothing bound, nothing in the hub, nothing shown.
  if (!discovery) return null;
  if (!discovery.has_any_hardware && bindings.length === 0) return null;

  const set = <K extends keyof GardenBindingIn>(key: K, value: GardenBindingIn[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));

  return (
    <div className={cardClass}>
      <h3 className="text-sm font-semibold text-slate-300 mb-3 uppercase tracking-wider">
        Home Hub Hardware &amp; Automation
      </h3>

      {own ? <BindingReadings binding={own} /> : (
        <p className="text-xs text-slate-500 italic">No sensors are bound to this yet.</p>
      )}

      {own?.moisture_override_enabled && own.readings?.moisture_pct != null && (
        <p className="mt-2 text-[11px] text-slate-300">
          {own.readings.moisture_pct < own.moisture_threshold_pct
            ? `Dry at ${own.readings.moisture_pct}% (below ${own.moisture_threshold_pct}%) — watering is due now.`
            : own.readings.moisture_pct >= own.moisture_threshold_pct + 15
              ? `Moist at ${own.readings.moisture_pct}% — watering is being held off.`
              : `Soil at ${own.readings.moisture_pct}% — the schedule applies.`}
        </p>
      )}

      {own && <div className="mt-3"><ValveControls binding={own} canEdit={canEdit} /></div>}

      {canEdit && (
        <details className="mt-3" open={false}>
          <summary className="cursor-pointer text-xs text-emerald-300 min-h-11 inline-flex items-center">
            {own ? 'Edit hardware' : 'Bind hardware'}
          </summary>
          <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-3">
            <EntityPicker
              id={`bind-moisture-${targetId}`}
              label="Soil moisture sensor"
              entities={discovery.moisture_sensors}
              value={draft.moisture_entity_id ?? null}
              onChange={(value) => set('moisture_entity_id', value)}
            />
            <EntityPicker
              id={`bind-temp-${targetId}`}
              label="Temperature sensor"
              entities={discovery.temperature_sensors}
              value={draft.temperature_entity_id ?? null}
              onChange={(value) => set('temperature_entity_id', value)}
            />
            <EntityPicker
              id={`bind-humidity-${targetId}`}
              label="Humidity sensor"
              entities={discovery.humidity_sensors}
              value={draft.humidity_entity_id ?? null}
              onChange={(value) => set('humidity_entity_id', value)}
            />
            <EntityPicker
              id={`bind-lux-${targetId}`}
              label="Light sensor"
              entities={discovery.lux_sensors}
              value={draft.lux_entity_id ?? null}
              onChange={(value) => set('lux_entity_id', value)}
            />
            <EntityPicker
              id={`bind-valve-${targetId}`}
              label="Irrigation valve"
              entities={discovery.valves}
              value={draft.valve_entity_id ?? null}
              onChange={(value) => set('valve_entity_id', value)}
            />
            <EntityPicker
              id={`bind-light-${targetId}`}
              label="Grow light"
              entities={discovery.grow_lights}
              value={draft.grow_light_entity_id ?? null}
              onChange={(value) => set('grow_light_entity_id', value)}
            />
          </div>
          <SaveControls draft={draft} set={set} discovery={discovery} onSave={() => save.mutate()} saving={save.isPending} />
          {own && (
            <button
              type="button"
              onClick={() => unbind.mutate()}
              disabled={unbind.isPending}
              className={`${buttonClass} mt-2 bg-rose-500/10 border-rose-500/30 text-rose-300 hover:bg-rose-500/20`}
            >
              {unbind.isPending ? 'Removing…' : 'Remove hardware'}
            </button>
          )}
        </details>
      )}
    </div>
  );
}

/** The safety caps, the soil override, the tag and the save button. */
function SaveControls({
  draft, set, discovery, onSave, saving,
}: {
  draft: GardenBindingIn;
  set: <K extends keyof GardenBindingIn>(key: K, value: GardenBindingIn[K]) => void;
  discovery: GardenHardwareDiscovery;
  onSave: () => void;
  saving: boolean;
}) {
  const toggleClass = 'inline-flex items-center gap-2 min-h-11 text-xs text-slate-300';
  return (
    <div className="mt-3 space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <label className={labelClass} htmlFor="bind-valve-mode">
          Valve mode
          <select
            id="bind-valve-mode"
            className={inputClass}
            value={draft.valve_mode ?? 'notify_only'}
            onChange={(event) => set('valve_mode', event.target.value as GardenBindingIn['valve_mode'])}
          >
            <option value="notify_only">Notify only (safest)</option>
            <option value="confirm">Confirm before running</option>
            <option value="auto">Auto (opt-in)</option>
          </select>
        </label>
        <label className={labelClass} htmlFor="bind-max-minutes">
          Max run minutes (1–30)
          <input
            id="bind-max-minutes"
            type="number"
            min={1}
            max={30}
            className={inputClass}
            value={draft.max_run_minutes ?? 5}
            onChange={(event) => set('max_run_minutes', Number(event.target.value))}
          />
        </label>
        <label className={labelClass} htmlFor="bind-daily-max">
          Daily max runs (1–4)
          <input
            id="bind-daily-max"
            type="number"
            min={1}
            max={4}
            className={inputClass}
            value={draft.daily_max_runs ?? 2}
            onChange={(event) => set('daily_max_runs', Number(event.target.value))}
          />
        </label>
      </div>

      <label className={toggleClass} htmlFor="bind-rain-interlock">
        <input
          id="bind-rain-interlock"
          type="checkbox"
          className="h-5 w-5"
          checked={draft.rain_interlock ?? true}
          onChange={(event) => set('rain_interlock', event.target.checked)}
        />
        Rain interlock: never water an uncovered bed in the rain
      </label>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className={toggleClass} htmlFor="bind-moisture-override">
          <input
            id="bind-moisture-override"
            type="checkbox"
            className="h-5 w-5"
            checked={draft.moisture_override_enabled ?? false}
            onChange={(event) => set('moisture_override_enabled', event.target.checked)}
          />
          Let the soil sensor decide when to water
        </label>
        <label className={labelClass} htmlFor="bind-moisture-threshold">
          Water below (%)
          <input
            id="bind-moisture-threshold"
            type="number"
            min={5}
            max={80}
            className={inputClass}
            value={draft.moisture_threshold_pct ?? 30}
            onChange={(event) => set('moisture_threshold_pct', Number(event.target.value))}
          />
        </label>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <label className={toggleClass} htmlFor="bind-grow-light-enabled">
          <input
            id="bind-grow-light-enabled"
            type="checkbox"
            className="h-5 w-5"
            checked={draft.grow_light_enabled ?? false}
            onChange={(event) => set('grow_light_enabled', event.target.checked)}
          />
          Grow light schedule
        </label>
        <label className={labelClass} htmlFor="bind-grow-light-on">
          On time
          <input
            id="bind-grow-light-on"
            type="time"
            className={inputClass}
            value={draft.grow_light_on_time ?? '06:00'}
            onChange={(event) => set('grow_light_on_time', event.target.value)}
          />
        </label>
        <label className={labelClass} htmlFor="bind-grow-light-off">
          Off time
          <input
            id="bind-grow-light-off"
            type="time"
            className={inputClass}
            value={draft.grow_light_off_time ?? '20:00'}
            onChange={(event) => set('grow_light_off_time', event.target.value)}
          />
        </label>
      </div>

      {discovery.tags.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-end">
          <label className={labelClass} htmlFor="bind-tag">
            NFC / QR tag
            <select
              id="bind-tag"
              className={inputClass}
              value={draft.nfc_tag_id ?? ''}
              onChange={(event) => set('nfc_tag_id', event.target.value || null)}
            >
              <option value="">No tag</option>
              {discovery.tags.map((tag) => (
                <option key={tag.tag_id} value={tag.tag_id}>
                  {tag.name || tag.tag_id}
                </option>
              ))}
            </select>
          </label>
          {draft.nfc_tag_id && (
            <a
              className="text-xs text-emerald-300 underline min-h-11 inline-flex items-center"
              href={`/garden?tag=${encodeURIComponent(draft.nfc_tag_id)}`}
            >
              Open the tag card (print this link as a QR code)
            </a>
          )}
        </div>
      )}

      <button
        type="button"
        onClick={onSave}
        disabled={saving}
        className={`${buttonClass} bg-emerald-500/10 border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/20`}
      >
        {saving ? 'Saving…' : 'Save hardware'}
      </button>
    </div>
  );
}
