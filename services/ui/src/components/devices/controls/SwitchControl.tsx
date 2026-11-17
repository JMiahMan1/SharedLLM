import { Gauge, Zap } from 'lucide-react';
import { GREEN, History, PANEL, PowerRow, type ControlProps } from './primitives';
import type { HubEntity } from '../../../lib/dashboards';
import { powerReadings } from '../../../lib/deviceControls';
import { isOff, readingText } from '../../../lib/deviceTiles';

/** What the device draws now and has used, from its own power and energy sensors, with the last day. */
export function PowerUsage({ siblings }: { siblings: HubEntity[] }) {
  const { power, energy } = powerReadings(siblings);
  if (!power && !energy) return null;
  return (
    <div className={PANEL} data-testid="power-usage">
      <div className="grid grid-cols-2 gap-3">
        {power && (
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-xs text-slate-400"><Zap size={13} aria-hidden /> Using now</p>
            <p className="mt-0.5 text-2xl font-semibold tabular-nums text-white">{isOff(power) ? 'Not known' : readingText(power)}</p>
          </div>
        )}
        {energy && (
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-xs text-slate-400"><Gauge size={13} aria-hidden /> Used in all</p>
            <p className="mt-0.5 text-2xl font-semibold tabular-nums text-white">{isOff(energy) ? 'Not known' : readingText(energy)}</p>
          </div>
        )}
      </div>
      {power && !isOff(power) && <History entityId={power.entity_id} unit={power.unit} colour={GREEN} />}
    </div>
  );
}

/** A switch or plug: a big toggle, then what it draws (W) and has used (kWh) when it measures them. */
export default function SwitchControl({ e, name, send, disabled, siblings }: ControlProps) {
  const on = e.state === 'on';
  return (
    <div className="space-y-3" data-testid="switch-control">
      <PowerRow on={on} name={name} disabled={disabled} accent={GREEN}
        onToggle={() => void send(on ? 'turn_off' : 'turn_on')} />
      <PowerUsage siblings={siblings} />
    </div>
  );
}
