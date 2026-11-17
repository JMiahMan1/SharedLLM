import type { CSSProperties } from 'react';
import { Pause, Play, Power, WifiOff } from 'lucide-react';
import DeviceGlyph from './DeviceGlyph';
import { accentFor, isActiveState } from '../../lib/deviceIcons';
import type { HubEntity } from '../../lib/dashboards';
import type { PhysicalDevice } from '../../lib/hardware';
import { useEntityCommand } from '../../lib/deviceCommand';
import {
  isOffline, primaryEntity, stateLine, tileAction, tileKind,
} from '../../lib/deviceTiles';

/**
 * One device on the grid: icon, name, one plain line of state, and a one-tap action for the
 * kinds that have one. The body opens the detail; the action is its own button beside it (not
 * inside it), so both are real buttons for a keyboard and a screen reader.
 */
export default function DeviceTile({ device, entities, onOpen }: {
  device: PhysicalDevice; entities: HubEntity[]; onOpen: () => void;
}) {
  const primary = primaryEntity(device.kind, entities);
  const { shown, send, busy } = useEntityCommand(primary, device.name);
  const kind = tileKind(device, shown);
  const offline = isOffline(device, shown);
  const action = offline ? null : tileAction(shown, device.name);
  const active = !offline && isActiveState(shown);
  const accent = accentFor(kind, shown);
  const line = stateLine(device, shown);
  const tint: CSSProperties | undefined = active
    ? { borderColor: accent.replace('rgb(', 'rgba(').replace(')', ', 0.45)'),
      backgroundImage: `linear-gradient(160deg, ${accent.replace('rgb(', 'rgba(').replace(')', ', 0.14)')}, transparent 70%)` }
    : undefined;

  return (
    <li className={`relative min-w-0 ${offline ? 'opacity-70' : ''}`} data-testid="device-tile" data-kind={kind}
      data-state={offline ? 'offline' : active ? 'on' : 'off'}>
      <div style={tint}
        className="relative flex h-full min-h-[7.25rem] flex-col justify-between gap-3 rounded-2xl border border-white/10 bg-white/[0.04] p-3 motion-safe:transition-colors hover:bg-white/[0.07]">
        <button type="button" onClick={onOpen} data-testid="hardware-device"
          aria-label={`${device.name}, ${line}`}
          className="absolute inset-0 rounded-2xl outline-none focus-visible:ring-2 focus-visible:ring-sky-400" />
        <div className="pointer-events-none relative flex items-start justify-between gap-2">
          <DeviceGlyph kind={kind} accent={accent} active={active} />
          {offline && (
            <span className="inline-flex items-center gap-1 rounded-full bg-rose-500/15 px-2 py-0.5 text-[11px] font-semibold text-rose-200">
              <WifiOff size={12} aria-hidden /> Offline
            </span>
          )}
        </div>
        <div className="pointer-events-none relative min-w-0 pr-12">
          <p className="line-clamp-2 break-words text-sm font-semibold leading-snug text-white" title={device.name}>{device.name}</p>
          <p className={`mt-0.5 truncate text-xs ${offline ? 'text-rose-200/90' : active ? 'text-slate-200' : 'text-slate-400'}`}
            data-testid="tile-state">{line}</p>
        </div>
        {action && (
          <button type="button" role={action.kind === 'toggle' ? 'switch' : undefined}
            aria-checked={action.kind === 'toggle' ? action.on : undefined}
            aria-pressed={action.kind === 'playpause' ? action.on : undefined}
            aria-label={action.label} disabled={busy}
            data-testid="tile-action"
            onClick={() => void send(action.service)}
            style={action.on ? { backgroundColor: accent, color: 'rgb(15, 23, 42)' } : undefined}
            className={`absolute bottom-3 right-3 z-[1] flex h-11 w-11 items-center justify-center rounded-full border outline-none motion-safe:transition-colors focus-visible:ring-2 focus-visible:ring-sky-400 disabled:opacity-60 ${
              action.on ? 'border-transparent shadow-lg' : 'border-white/15 bg-white/[0.06] text-slate-300 hover:bg-white/10'}`}>
            {action.kind === 'playpause' ? (action.on ? <Pause size={18} /> : <Play size={18} />) : <Power size={18} />}
          </button>
        )}
      </div>
    </li>
  );
}
