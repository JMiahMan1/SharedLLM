import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BellRing, ChevronDown, DoorClosed, DoorOpen, Fan, Lightbulb, Loader2, Lock, LockOpen, Power, Settings2, Square,
  Warehouse,
} from 'lucide-react';
import { runService } from '../../lib/dashboards';
import { errorText } from '../../lib/errorText';
import {
  actionService, doorIsOpen, getCameraControls, isOffline, shortName, SIREN_SECONDS, statusAlert, statusText,
  type CameraControl,
} from '../../lib/cameraControls';
import { confirmDialog } from '../ui/confirmDialog';
import { Toggle } from '../ui/Toggle';

/** How often the panel re-reads states while the camera is open (a door moving, a detection). */
const POLL_MS = 3_000;
const BIG = 'min-h-14 w-full flex items-center gap-3 rounded-2xl border px-4 py-2 text-left transition-colors disabled:opacity-50';

function ActionIcon({ item }: { item: CameraControl }) {
  const size = 22;
  if (item.role === 'opener') return doorIsOpen(item.door) ? <DoorOpen size={size} /> : <DoorClosed size={size} />;
  switch (item.role) {
    case 'siren': return <BellRing size={size} />;
    case 'light': return <Lightbulb size={size} />;
    case 'lock': return item.state === 'locked' ? <Lock size={size} /> : <LockOpen size={size} />;
    case 'cover': return <Warehouse size={size} />;
    case 'fan': return <Fan size={size} />;
    default: return <Power size={size} />;
  }
}

/**
 * Only "unavailable" means an action cannot be used: sirens and buttons keep no state, so a Reolink siren
 * always reports "unknown" and must still sound.
 */
const unreachable = (item: { state: string }) => item.state === 'unavailable';

/** The one-line state under an action's name. */
function actionState(item: CameraControl): string {
  if (item.role === 'opener') return item.door && !isOffline(item.door) ? (doorIsOpen(item.door) ? 'Open' : 'Closed') : 'State unknown';
  if (unreachable(item)) return 'Not answering';
  if (item.role === 'siren') return item.state === 'on' ? 'Sounding' : 'Quiet';
  if (item.domain === 'button') return 'Press';
  if (item.domain === 'lock') return item.state === 'locked' ? 'Locked' : item.state === 'unlocked' ? 'Unlocked' : item.state;
  if (item.domain === 'cover' || item.domain === 'valve') return item.state[0].toUpperCase() + item.state.slice(1);
  return item.state === 'on' ? 'On' : 'Off';
}

/** What pressing it does, for its accessible name. */
function actionVerb(item: CameraControl, name: string): string {
  if (item.role === 'opener') return `${doorIsOpen(item.door) ? 'Close' : 'Open'} ${name}`;
  if (item.role === 'siren') return item.state === 'on' ? `Stop the siren` : `Sound the siren`;
  const [, service] = actionService(item);
  const verb: Record<string, string> = {
    turn_on: 'Turn on', turn_off: 'Turn off', press: 'Press', lock: 'Lock', unlock: 'Unlock',
    open_cover: 'Open', close_cover: 'Close', open_valve: 'Open', close_valve: 'Close',
  };
  return `${verb[service] ?? service} ${name}`;
}

function QuickAction({ item, cameraName, onDone }: { item: CameraControl; cameraName: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const name = shortName(item.name, cameraName);
  const label = actionVerb(item, name);
  const active = item.role === 'opener' ? doorIsOpen(item.door) : ['on', 'open', 'unlocked'].includes(item.state);
  const press = async () => {
    // Moving a door, sounding a siren or unlocking is never one stray tap.
    if (item.role === 'opener') {
      const ok = await confirmDialog({
        title: `${doorIsOpen(item.door) ? 'Close' : 'Open'} ${name}?`,
        body: `This moves the ${name.toLowerCase()} now. Make sure nothing and nobody is in the way.`,
        confirmLabel: doorIsOpen(item.door) ? 'Close' : 'Open',
      });
      if (!ok) return;
    } else if (item.role === 'siren' && item.state !== 'on') {
      const ok = await confirmDialog({
        title: `Sound the siren on ${cameraName}?`, danger: true, confirmLabel: 'Sound siren',
        body: `It is very loud and sounds for ${SIREN_SECONDS} seconds, or until you stop it.`,
      });
      if (!ok) return;
    } else if (item.domain === 'lock' && item.state === 'locked') {
      const ok = await confirmDialog({ title: `Unlock ${name}?`, body: 'Anyone at the door can then open it.', confirmLabel: 'Unlock' });
      if (!ok) return;
    }
    setBusy(true);
    const [domain, service] = item.role === 'siren'
      ? ['siren', item.state === 'on' ? 'turn_off' : 'turn_on'] : actionService(item);
    const data = item.role === 'siren' && service === 'turn_on' ? { duration: SIREN_SECONDS } : undefined;
    await runService(domain, service, item.entity_id, data);
    setBusy(false);
    onDone();
  };
  const tone = item.role === 'siren'
    ? (active ? 'bg-red-500/25 border-red-500/60 text-red-100' : 'bg-red-500/10 border-red-500/30 text-red-100 hover:bg-red-500/20')
    : active ? 'bg-amber-500/20 border-amber-500/50 text-amber-50' : 'bg-white/5 border-white/10 text-white hover:bg-white/10';
  return (
    <button type="button" onClick={() => void press()} disabled={busy || (unreachable(item) && item.role !== 'opener')}
      aria-label={label} className={`${BIG} ${tone}`} data-testid="camera-action" data-role={item.role}>
      <span className="shrink-0" aria-hidden>{busy ? <Loader2 size={22} className="animate-spin" /> : <ActionIcon item={item} />}</span>
      <span className="min-w-0 flex-1">
        <span className="block break-words text-sm font-medium">{name}</span>
        <span className="block text-xs opacity-80">{actionState(item)}</span>
      </span>
      {item.role === 'siren' && item.state === 'on' && <Square size={16} aria-hidden />}
    </button>
  );
}

function StatusChips({ items, cameraName }: { items: CameraControl[]; cameraName: string }) {
  return (
    <ul className="flex flex-wrap gap-2" aria-label="What the camera reports">
      {items.map((item) => {
        const alert = statusAlert(item);
        return (
          <li key={item.entity_id} data-testid="camera-status"
            className={`min-h-9 flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs ${
              alert ? 'bg-amber-500/15 border-amber-500/40 text-amber-100' : 'bg-white/5 border-white/10 text-slate-300'}`}>
            {alert && <span className="h-1.5 w-1.5 rounded-full bg-amber-400" aria-hidden />}
            <span className="break-words">{shortName(item.name, cameraName)}:</span>
            <span className="font-medium text-white">{statusText(item)}</span>
          </li>
        );
      })}
    </ul>
  );
}

function SettingRow({ item, cameraName, onDone }: { item: CameraControl; cameraName: string; onDone: () => void }) {
  const name = shortName(item.name, cameraName);
  const [value, setValue] = useState<number | null>(null);
  const set = async (domain: string, service: string, data?: Record<string, unknown>) => {
    await runService(domain, service, item.entity_id, data);
    onDone();
  };
  let control: React.ReactNode = <span className="text-xs text-slate-400">{statusText(item)}</span>;
  if (item.domain === 'switch' || item.domain === 'light' || item.domain === 'siren' || item.domain === 'fan') {
    control = <Toggle checked={item.state === 'on'} disabled={isOffline(item)} ariaLabel={name}
      onChange={(on) => void set(item.domain, on ? 'turn_on' : 'turn_off')} />;
  } else if (item.domain === 'number') {
    const a = item.attributes;
    const min = Number(a.min ?? 0), max = Number(a.max ?? 100), step = Number(a.step ?? 1);
    const current = value ?? Number(item.state);
    control = (
      <span className="flex w-full items-center gap-3 sm:w-64">
        <input type="range" min={min} max={max} step={step} value={Number.isFinite(current) ? current : min}
          disabled={isOffline(item)} aria-label={name} className="min-h-11 flex-1 accent-cyan-400"
          onChange={(e) => setValue(Number(e.target.value))}
          onPointerUp={() => value !== null && void set('number', 'set_value', { value })}
          onKeyUp={() => value !== null && void set('number', 'set_value', { value })} />
        <span className="w-10 text-right text-xs tabular-nums text-slate-200">{Number.isFinite(current) ? current : '–'}</span>
      </span>
    );
  } else if (item.domain === 'select') {
    const options = Array.isArray(item.attributes.options) ? (item.attributes.options as unknown[]).map(String) : [];
    control = (
      <select value={item.state} disabled={isOffline(item)} aria-label={name}
        onChange={(e) => void set('select', 'select_option', { option: e.target.value })}
        className="min-h-11 rounded-xl bg-white/5 border border-white/10 px-3 text-sm text-white">
        {options.map((o) => <option key={o} value={o}>{o.replace(/_/g, ' ')}</option>)}
      </select>
    );
  }
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 py-2" data-testid="camera-setting">
      <span className="min-w-0 flex-1 break-words text-sm text-slate-200">{name}</span>
      {control}
    </li>
  );
}

/**
 * Camera viewer > what the camera's device can do: quick actions (a door
 * opener, the siren, lights, locks...) as large buttons right under the
 * picture, what it reports as chips, and for admins the device's settings.
 * Found by the hub for any camera (feature discovery), so a new camera shows
 * its features without being set up. Each action goes through the same
 * permission-checked service call as everywhere else.
 */
export default function CameraControls({ entityId, cameraName, isAdmin }: {
  entityId: string; cameraName: string; isAdmin: boolean;
}) {
  const queryClient = useQueryClient();
  const key = ['camera-controls', entityId];
  const controls = useQuery({
    queryKey: key, queryFn: () => getCameraControls(entityId),
    refetchInterval: () => (document.hidden ? false : POLL_MS),
  });
  const refresh = () => { void queryClient.invalidateQueries({ queryKey: key }); };

  if (controls.isLoading) {
    return <div className="flex justify-center py-4" role="status" aria-label="Loading the camera's controls"><Loader2 className="animate-spin text-slate-500" /></div>;
  }
  if (controls.isError || !controls.data) {
    return <p className="text-sm text-red-300">{errorText(controls.error, 'Couldn’t read what this camera can do')}</p>;
  }
  const { actions, status, settings } = controls.data;
  if (!actions.length && !status.length && !settings.length) return null;

  return (
    <section className="space-y-3" aria-label="Camera controls" data-testid="camera-controls">
      {actions.length > 0 && (
        <div className="grid grid-cols-1 gap-2 min-[420px]:grid-cols-2">
          {actions.map((item) => <QuickAction key={item.entity_id} item={item} cameraName={cameraName} onDone={refresh} />)}
        </div>
      )}
      {status.length > 0 && <StatusChips items={status} cameraName={cameraName} />}
      {isAdmin && settings.length > 0 && (
        <details className="glass-panel rounded-2xl p-4 group">
          <summary className="min-h-11 flex cursor-pointer list-none items-center gap-2 text-sm font-medium text-white">
            <ChevronDown size={16} className="transition-transform group-open:rotate-180" aria-hidden />
            <Settings2 size={16} aria-hidden />Camera settings
          </summary>
          <ul className="divide-y divide-white/5 pt-2" aria-label="Camera settings">
            {settings.map((item) => <SettingRow key={item.entity_id} item={item} cameraName={cameraName} onDone={refresh} />)}
          </ul>
        </details>
      )}
    </section>
  );
}
