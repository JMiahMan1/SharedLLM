import { useState } from 'react';
import toast from 'react-hot-toast';
import { CalendarClock, Pause, Play, PowerOff } from 'lucide-react';
import { confirmDialog } from '../ui/confirmDialog';
import Modal from '../ui/Modal';
import HoldControl from './HoldControl';
import { errorText } from '../../lib/errorText';
import { holdText, nextChangeText, resumeThermostat, setScheduleEnabled, type Thermostat } from '../../lib/schedules';

/**
 * One thermostat's schedule in a sentence ("Following Winter Week Day: 73, goes
 * to 78 at 5 PM", the hub's own summary), whether it is held, and Hold / Resume.
 * Shared by the Climate page and the home overview so a family member can hold
 * the heat without finding the Schedules page; Hold opens the same control
 * Schedules uses, in a sheet (a bottom sheet on a phone). An admin can also turn
 * the program it follows off from here (on again from Schedules).
 */
export default function ScheduleStatus({ thermostat, onChanged, compact = false, canEdit = false }: {
  thermostat: Thermostat;
  onChanged: () => void;
  /** One line and the buttons only, for an overview card. */
  compact?: boolean;
  /** An admin: may turn the program this thermostat follows off. */
  canEdit?: boolean;
}) {
  const [holding, setHolding] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [stopping, setStopping] = useState(false);
  const program = thermostat.current ?? thermostat.next;
  const name = thermostat.name || 'Thermostat';
  const held = holdText(thermostat);

  async function resume() {
    setResuming(true);
    try {
      await resumeThermostat(thermostat.entity_id);
      toast.success(`${name} follows its schedule again`);
      onChanged();
    } catch (err) {
      toast.error(errorText(err, 'Resuming did not work'));
    } finally {
      setResuming(false);
    }
  }

  async function turnOff() {
    if (!program) return;
    const ok = await confirmDialog({
      title: `Turn off ${program.program_name}?`,
      body: 'It stops changing every thermostat it runs, which stay as they are now. Turn it back on in Schedules.',
      confirmLabel: 'Turn off',
    });
    if (!ok) return;
    setStopping(true);
    try {
      await setScheduleEnabled(program.program_id, false);
      toast.success(`${program.program_name} is off`);
      onChanged();
    } catch (err) {
      toast.error(errorText(err, 'Turning it off did not work'));
    } finally {
      setStopping(false);
    }
  }

  return (
    <div className="space-y-2" data-testid="schedule-status">
      <p className={`flex items-start gap-2 text-slate-300 ${compact ? 'text-xs' : 'text-sm'}`}>
        <CalendarClock size={compact ? 13 : 15} className="mt-0.5 shrink-0 text-sky-400" aria-hidden />
        <span>{held || nextChangeText(thermostat)}</span>
      </p>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => setHolding(true)} aria-label={`Hold ${name}`}
          className="inline-flex min-h-11 items-center gap-1.5 rounded-xl border border-white/10 bg-white/5 px-3 text-sm text-slate-200 hover:bg-white/10">
          <Pause size={14} aria-hidden />{thermostat.hold ? 'Change hold' : 'Hold'}
        </button>
        {thermostat.hold && (
          <button type="button" onClick={() => void resume()} disabled={resuming} aria-label={`Resume the schedule of ${name}`}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-xl border border-sky-500/30 bg-sky-500/10 px-3 text-sm text-sky-100 disabled:opacity-50">
            <Play size={14} aria-hidden />Resume schedule
          </button>
        )}
        {canEdit && !compact && program && (
          <button type="button" onClick={() => void turnOff()} disabled={stopping} aria-label={`Turn off the ${program.program_name} schedule`}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-xl border border-white/10 bg-white/5 px-3 text-sm text-slate-200 hover:bg-white/10 disabled:opacity-50">
            <PowerOff size={14} aria-hidden />Turn off {program.program_name}
          </button>
        )}
      </div>
      <Modal isOpen={holding} onClose={() => setHolding(false)} title={`Hold ${name}`} size="md">
        <HoldControl thermostat={thermostat} onChanged={() => { setHolding(false); onChanged(); }} />
      </Modal>
    </div>
  );
}
