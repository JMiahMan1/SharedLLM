import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Download, Radio, Settings2 } from 'lucide-react';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import { extractEchoFleet, type EchoDeviceSummary, type EchoState } from '../../lib/echoFleet';

/** 44px on coarse pointers, dense on desktop (CONTRIBUTING.md, mobile parity). */
const TOUCH = 'min-h-11 pointer-coarse:min-h-11';
const FLEET_STATES_KEY = ['echo-fleet', 'states'];

export interface EchoFleetPanelProps {
  /** When omitted the panel reads Home Hub states itself. */
  states?: EchoState[];
  /** Overrides starting a Drop-In; ending one always presses hassmic's button entity. */
  onDropIn?: (device: EchoDeviceSummary) => void | Promise<void>;
}

function messageError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function entityDomain(entityId: string): string {
  return entityId.split('.', 1)[0] ?? '';
}

function dropInActive(device: EchoDeviceSummary): boolean {
  return device.dropInState === 'connected' || device.dropInState === 'calling' || device.dropInState === 'ringing';
}

function statusLabel(device: EchoDeviceSummary): string {
  if (!device.online) return 'Offline';
  switch (device.dropInState) {
    case 'connected': return 'Drop-In connected';
    case 'calling': return 'Calling';
    case 'ringing': return 'Ringing';
    case 'idle': return 'Ready';
    default: return 'Status unknown';
  }
}

/**
 * The fleet card used on Devices. Home Hub is the only source of truth: when a
 * control entity is missing the card names the gap instead of hiding it, and a
 * failed service call surfaces verbatim rather than reverting to an optimistic lie.
 */
export default function EchoFleetPanel({ states: providedStates, onDropIn }: EchoFleetPanelProps) {
  const haptics = useHaptics();
  const queryClient = useQueryClient();
  const [dndOverrides, setDndOverrides] = useState<Record<string, boolean>>({});
  const [wakeWordOverrides, setWakeWordOverrides] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [updateRequested, setUpdateRequested] = useState<string[]>([]);
  const [updatingAll, setUpdatingAll] = useState(false);

  const statesQuery = useQuery({
    queryKey: FLEET_STATES_KEY,
    queryFn: () => api.getEntities(),
    enabled: providedStates === undefined,
    retry: 1,
  });

  const states = providedStates ?? statesQuery.data ?? [];
  const fleet = useMemo(() => extractEchoFleet(states), [states]);
  const loading = providedStates === undefined && statesQuery.isPending;
  const loadError = providedStates === undefined && statesQuery.isError
    ? messageError(statesQuery.error)
    : null;
  const updatesCount = fleet.filter((d) => d.updateAvailable).length;

  const runAction = async (key: string, label: string, task: () => Promise<unknown>) => {
    setActionError(null);
    setBusy(key);
    try {
      await task();
      await haptics.trigger('light');
    } catch (error) {
      setActionError(`${label}: ${messageError(error)}`);
    } finally {
      setBusy(null);
    }
  };

  const toggleDnd = (device: EchoDeviceSummary) => {
    if (!device.dndEntityId) {
      setActionError(`Do Not Disturb is not exposed by Home Hub for ${device.name}. Publish a dnd switch for this node.`);
      return;
    }
    const next = !(dndOverrides[device.dndEntityId] ?? device.dndOn);
    void runAction(`dnd:${device.node}`, `Could not change Do Not Disturb on ${device.name}`, async () => {
      await api.callHaService(
        entityDomain(device.dndEntityId!),
        next ? 'turn_on' : 'turn_off',
        device.dndEntityId!,
      );
      setDndOverrides((current) => ({ ...current, [device.dndEntityId!]: next }));
      await queryClient.invalidateQueries({ queryKey: FLEET_STATES_KEY });
    });
  };

  const changeWakeWord = (device: EchoDeviceSummary, option: string) => {
    if (!device.wakeWordEntityId || !option) return;
    void runAction(`wake:${device.node}`, `Could not change the wake word on ${device.name}`, async () => {
      await api.callHaService(
        entityDomain(device.wakeWordEntityId!),
        'select_option',
        device.wakeWordEntityId!,
        { option },
      );
      setWakeWordOverrides((current) => ({ ...current, [device.wakeWordEntityId!]: option }));
      await queryClient.invalidateQueries({ queryKey: FLEET_STATES_KEY });
    });
  };

  const toggleDropIn = (device: EchoDeviceSummary) => {
    const active = dropInActive(device);
    if (active && !device.endDropInEntityId) {
      setActionError(`Home Hub has no end_drop_in button entity for ${device.name} (node ${device.node}). Publish button.${device.node}_end_drop_in to end Drop-In.`);
      return;
    }
    const entityId = device.dropInEntityId ?? device.satelliteEntityId;
    if (!active && !entityId) {
      setActionError(`Drop-In is unavailable for ${device.name}: Home Hub has published neither a drop-in sensor nor a satellite entity.`);
      return;
    }
    void runAction(`dropin:${device.node}`, `Could not ${active ? 'end' : 'start'} Drop-In on ${device.name}`, async () => {
      if (active) {
        await api.callHaService('button', 'press', device.endDropInEntityId!);
      } else if (onDropIn) {
        await onDropIn(device);
      } else {
        // hassmic's ESPHome node exposes `<node>_drop_in`, not a hassmic domain service.
        await api.callHaService('esphome', `${device.node}_drop_in`, entityId ?? '', { target: device.node });
      }
      await queryClient.invalidateQueries({ queryKey: FLEET_STATES_KEY });
    });
  };

  const installUpdate = (device: EchoDeviceSummary) => {
    if (!device.updateEntityId) {
      setActionError(`No firmware update entity is published for ${device.name}.`);
      return;
    }
    void runAction(`update:${device.node}`, `Could not install the update on ${device.name}`, async () => {
      await api.callHaService('update', 'install', device.updateEntityId!);
      setUpdateRequested((current) => [...new Set([...current, device.node])]);
      await queryClient.invalidateQueries({ queryKey: FLEET_STATES_KEY });
    });
  };

  const updateAll = async () => {
    setActionError(null);
    setUpdatingAll(true);
    try {
      await api.updateAllEchos();
      await queryClient.invalidateQueries({ queryKey: FLEET_STATES_KEY });
    } catch (error) {
      setActionError(`Could not queue updates for all Echos: ${messageError(error)}`);
    } finally {
      setUpdatingAll(false);
    }
  };

  return (
    <section className="space-y-4" aria-label="Echo fleet">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-100">
            <Radio size={18} aria-hidden /> Echo fleet
          </h2>
          <p className="text-xs text-slate-400">
            Echo Dot and Echo Show nodes adopted through Home Hub (hassmic / echo-dot-assist).
          </p>
        </div>
        <div className="flex items-center gap-2">
          {updatesCount >= 2 && (
            <button
              type="button"
              onClick={updateAll}
              disabled={updatingAll}
              className={`${TOUCH} rounded-xl bg-amber-500 px-3 text-sm font-semibold text-amber-950 hover:bg-amber-400 disabled:opacity-50`}
            >
              {updatingAll ? 'Queuing...' : 'Update all Echos'}
            </button>
          )}
          <span className="rounded-full bg-slate-800 px-3 py-1 text-xs text-slate-300" data-testid="echo-fleet-count">
            {fleet.length} {fleet.length === 1 ? 'device' : 'devices'}
          </span>
        </div>
      </div>

      {loading && <p role="status" className="rounded-xl bg-slate-800/60 p-4 text-sm text-slate-300">Loading Home Hub states…</p>}
      {loadError && (
        <p role="alert" className="flex items-start gap-2 rounded-xl border border-red-500/40 bg-red-950/50 p-4 text-sm text-red-100">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
          Home Hub is unavailable: {loadError}
        </p>
      )}
      {actionError && (
        <p role="alert" className="flex items-start gap-2 rounded-xl border border-red-500/40 bg-red-950/50 p-4 text-sm text-red-100">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
          {actionError}
        </p>
      )}

      {!loading && !loadError && fleet.length === 0 && (
        <div role="status" className="rounded-2xl border border-dashed border-slate-700 bg-slate-900/60 p-6 text-sm text-slate-300">
          <p className="text-base font-semibold text-slate-100">No Echo devices found</p>
          <p className="mt-2 max-w-2xl leading-relaxed">
            Adopt an Echo Dot with the <code className="rounded bg-slate-800 px-1">donut</code> project or an
            Echo Show with <code className="rounded bg-slate-800 px-1">crown</code>, then add the
            <code className="mx-1 rounded bg-slate-800 px-1">echo-dot-assist</code> (hassmic) node so Home Hub
            publishes its drop-in and satellite entities.
          </p>
        </div>
      )}

      {fleet.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {fleet.map((device) => {
            const dndOn = dndOverrides[device.dndEntityId ?? ''] ?? device.dndOn;
            const active = dropInActive(device);
            const wakeWord = wakeWordOverrides[device.wakeWordEntityId ?? ''] ?? device.wakeWord;
            const wakeOptionsRaw = device.wakeWordOptions.length > 0
              ? device.wakeWordOptions
              : wakeWord ? [wakeWord] : [];
            const wakeOptions = wakeWord && !wakeOptionsRaw.includes(wakeWord)
              ? [wakeWord, ...wakeOptionsRaw]
              : wakeOptionsRaw;
            return (
              <article
                key={device.node}
                data-testid={`echo-device-${device.node}`}
                className="flex min-w-[280px] flex-col gap-3 rounded-2xl border border-slate-700/70 bg-slate-900/70 p-4 text-slate-200"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate text-base font-semibold text-white">{device.name}</h3>
                    <p className="truncate text-[11px] text-slate-400">node: {device.node}</p>
                  </div>
                  <span
                    className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold ${
                      device.online ? 'bg-emerald-500/15 text-emerald-300' : 'bg-red-500/15 text-red-300'
                    }`}
                  >
                    {statusLabel(device)}
                  </span>
                </div>

                {device.dropInWith && (
                  <p className="text-xs text-slate-300">Drop-In with {device.dropInWith}</p>
                )}

                <div className={`${TOUCH} flex items-center justify-between gap-3 rounded-xl bg-slate-800/70 px-3`}>
                  <span className="text-sm">Do Not Disturb <span className="text-slate-400">({dndOn ? 'on' : 'off'})</span></span>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={dndOn}
                    aria-label={`Do Not Disturb for ${device.name}`}
                    disabled={!device.dndEntityId || busy === `dnd:${device.node}`}
                    onClick={() => toggleDnd(device)}
                    className={`${TOUCH} min-w-11 rounded-lg border border-slate-600 px-2 text-xs font-semibold disabled:opacity-50 ${
                      dndOn ? 'bg-amber-500/25 text-amber-200' : 'bg-slate-700 text-slate-300'
                    }`}
                  >
                    {dndOn ? 'On' : 'Off'}
                  </button>
                </div>
                {!device.dndEntityId && (
                  <p role="status" className="text-[11px] text-amber-300">
                    Do Not Disturb control is not exposed by Home Hub for this node.
                  </p>
                )}

                <label className="block">
                  <span className="text-xs font-medium text-slate-300">Wake word</span>
                  <select
                    aria-label={`Wake word for ${device.name}`}
                    className={`${TOUCH} mt-1 w-full rounded-xl border border-slate-700 bg-slate-800 px-3 text-sm text-slate-100 disabled:opacity-50`}
                    value={wakeWord ?? ''}
                    disabled={!device.wakeWordEntityId || wakeOptions.length === 0}
                    onChange={(event) => changeWakeWord(device, event.target.value)}
                  >
                    {wakeOptions.length === 0 && <option value="">Not reported by Home Hub</option>}
                    {wakeOptions.map((option) => <option key={option} value={option}>{option}</option>)}
                  </select>
                </label>
                {(!device.wakeWordEntityId || wakeOptions.length === 0) && (
                  <p role="status" className="text-[11px] text-amber-300">
                    No wake word select entity is published for this node.
                  </p>
                )}

                <button
                  type="button"
                  aria-label={`${active ? 'End Drop In on' : 'Drop In on'} ${device.name}`}
                  onClick={() => toggleDropIn(device)}
                  className={`${TOUCH} w-full rounded-xl px-3 text-sm font-semibold disabled:opacity-50 ${
                    active ? 'bg-red-500/20 text-red-200 hover:bg-red-500/30' : 'bg-indigo-500 text-white hover:bg-indigo-400'
                  }`}
                >
                  {active ? 'End Drop In' : 'Drop In'}
                </button>

                <div className="rounded-xl bg-slate-800/50 p-3 text-xs text-slate-300">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span>Firmware</span>
                    {device.updateAvailable
                      ? <span className="rounded-full bg-amber-500/20 px-2 py-0.5 font-semibold text-amber-200">Update available</span>
                      : <span className="text-slate-400">Up to date</span>}
                  </div>
                  {device.installedVersion && <p className="mt-1 text-slate-400">Installed: {device.installedVersion}</p>}
                  {device.updateAvailable && device.latestVersion && <p className="mt-1 text-slate-400">Latest: {device.latestVersion}</p>}
                  {device.updateAvailable && (
                    <button
                      type="button"
                      aria-label={`Install firmware update for ${device.name}`}
                      onClick={() => installUpdate(device)}
                      className={`${TOUCH} mt-2 flex w-full items-center justify-center gap-2 rounded-xl bg-amber-500 px-3 text-sm font-semibold text-amber-950 hover:bg-amber-400 disabled:opacity-50`}
                    >
                      <Download size={15} aria-hidden /> Install
                    </button>
                  )}
                  {updateRequested.includes(device.node) && (
                    <p role="status" className="mt-1 text-emerald-300">Install requested; the hub will report the new version.</p>
                  )}
                </div>

                {device.webUiUrl ? (
                  <a
                    href={device.webUiUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`${TOUCH} flex items-center justify-center gap-2 rounded-xl border border-slate-700 px-3 text-sm font-medium text-slate-200 hover:bg-slate-800`}
                  >
                    <Settings2 size={15} aria-hidden /> Echo Settings
                  </a>
                ) : (
                  <p role="status" className="text-[11px] text-amber-300">
                    Echo Settings URL is not published or is not a safe http(s) address.
                  </p>
                )}

                {device.btPairingEntityId && (
                  <div className={`${TOUCH} flex items-center justify-between gap-2 rounded-xl bg-slate-800/70 px-3 pt-2 pb-1 text-xs text-slate-300`}
                      aria-label={`Bluetooth speaker for ${device.name}`}>
                    <span>Bluetooth Speaker</span>
                    {device.btPairingOn ? (
                      <span className="text-emerald-300">Paired</span>
                    ) : (
                      <button
                        type="button"
                        aria-label={`Pair Bluetooth on ${device.name}`}
                        onClick={() => api.callHaService('switch', 'turn_on', device.btPairingEntityId!)}
                        className={`${TOUCH} rounded-lg border border-slate-600 px-2 text-xs font-semibold disabled:opacity-50 ${
                          device.btPairingOn ? 'bg-amber-500/25 text-amber-200' : 'bg-slate-700 text-slate-300'
                        }`}
                      >
                        Pair
                      </button>
                    )}
                    {device.btDisconnectEntityId && (
                      <button
                        type="button"
                        aria-label={`Disconnect Bluetooth on ${device.name}`}
                        onClick={() => api.callHaService('button', 'press', device.btDisconnectEntityId!)}
                        className={`${TOUCH} rounded-lg border border-slate-600 px-2 text-xs font-semibold disabled:opacity-50`}
                      >
                        Disconnect
                      </button>
                    )}
                    {device.btConnectedDevice && (
                      <span className="text-[11px] text-slate-400">
                       Connected: {device.btConnectedDevice}
                      </span>
                    )}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
