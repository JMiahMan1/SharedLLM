import { useMemo, useState } from 'react';
import { Play, Thermometer } from 'lucide-react';
import { BUTTON } from '../automations/styles';
import HoldControl from './HoldControl';
import WeekTimeline from './WeekTimeline';
import { actionTone, formatTemp } from '../../lib/climate';
import {
  holdText, nextChangeText, resumeThermostat, setpointText, weekdayOf, WEEKDAYS, weekPrograms,
  type Program, type Thermostat,
} from '../../lib/schedules';
import toast from 'react-hot-toast';
import { errorText } from '../../lib/errorText';

const SOURCE_LABELS: Record<string, string> = {
  hold: 'Held',
  away: 'Away',
  energy: 'Saving energy',
  schedule: 'On schedule',
  none: 'No schedule',
};

/**
 * One thermostat: what it reads, which slot of which program it is in, what
 * changes next, a hold control, and its week. Tapping a slot opens it (to edit
 * for an admin, to read for everyone else).
 */
export default function ThermostatCard({ thermostat, programs, now, onSlot, onChanged }: {
  thermostat: Thermostat;
  programs: Program[];
  now: Date;
  onSlot: (program: Program, slot: number) => void;
  onChanged: () => void;
}) {
  const [holding, setHolding] = useState(false);
  const week = useMemo(() => weekPrograms(programs, thermostat.entity_id, now), [programs, thermostat.entity_id, now]);
  const unit = thermostat.capabilities?.unit ?? '°';
  const tone = actionTone(thermostat.hvac_action ?? undefined, thermostat.state);
  const name = thermostat.name || thermostat.entity_id;
  const source = thermostat.effective?.source ?? 'none';

  async function resume() {
    try {
      await resumeThermostat(thermostat.entity_id);
      onChanged();
    } catch (err) {
      toast.error(errorText(err, 'Resuming did not work'));
    }
  }

  return (
    <article className="glass-panel space-y-3 rounded-2xl border border-white/10 p-4" data-testid="thermostat-card" aria-label={name}>
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate text-base font-semibold text-white">{name}</h2>
          {thermostat.area && <p className="text-xs text-slate-500">{thermostat.area}</p>}
        </div>
        <div className="text-right">
          <p className="flex items-center justify-end gap-1 text-2xl font-bold text-white">
            <Thermometer size={18} className="text-slate-400" aria-hidden />
            {formatTemp(thermostat.current_temperature ?? undefined)}{unit.startsWith('°') ? '°' : ''}
          </p>
          <p className={`text-xs ${tone.text}`}>{tone.label}</p>
        </div>
      </header>

      <div className="space-y-1 text-sm">
        <p className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-white/10 px-2 py-0.5 text-[11px] text-slate-300">{SOURCE_LABELS[source] ?? source}</span>
          {thermostat.current && (
            <span className="text-slate-200">
              {thermostat.current.program_name}: {setpointText(thermostat.current.setpoint, unit)}
            </span>
          )}
        </p>
        {thermostat.hold && <p className="text-amber-200">{holdText(thermostat, now)}</p>}
        <p className="text-slate-400" data-testid="next-change">{nextChangeText(thermostat, now)}</p>
      </div>

      <div className="flex flex-wrap gap-2">
        <button type="button" className={BUTTON} aria-expanded={holding} onClick={() => setHolding((v) => !v)}>
          {holding ? 'Close hold' : 'Hold a temperature'}
        </button>
        {thermostat.hold && !holding && (
          <button type="button" className={BUTTON} onClick={() => void resume()}><Play size={14} /> Resume schedule</button>
        )}
      </div>
      {holding && <HoldControl thermostat={thermostat} onChanged={() => { setHolding(false); onChanged(); }} />}

      <WeekTimeline week={week} unit={unit} todayIndex={WEEKDAYS.indexOf(weekdayOf(now))}
        onSlot={(program, slot) => onSlot(program, slot)} />
    </article>
  );
}
