import { useState } from 'react';
import toast from 'react-hot-toast';
import { Loader2, Pause, Play } from 'lucide-react';
import { Field } from '../automations/FormBits';
import { BUTTON, INPUT, PRIMARY } from '../automations/styles';
import SetpointFields from './SetpointFields';
import { errorText } from '../../lib/errorText';
import {
  defaultSetpoint, holdThermostat, holdUntilOptions, holdUntilValue, resumeThermostat,
  type HoldOption, type Setpoint, type Thermostat,
} from '../../lib/schedules';

/**
 * Hold a thermostat at a setpoint until the next scheduled change, until a
 * time, or until someone resumes it; and resume. Anyone who may control the
 * thermostat can do this (the gateway checks), not only admins.
 */
export default function HoldControl({ thermostat, onChanged }: { thermostat: Thermostat; onChanged: () => void }) {
  const options = holdUntilOptions(thermostat);
  const [setpoint, setSetpoint] = useState<Setpoint>(() => thermostat.hold?.setpoint ?? thermostat.effective?.setpoint ?? defaultSetpoint(thermostat));
  const [choice, setChoice] = useState<HoldOption['value']>(() => (thermostat.next ? 'next_slot' : 'permanent'));
  const [time, setTime] = useState('');
  const [busy, setBusy] = useState(false);
  const name = thermostat.name || thermostat.entity_id;

  async function hold() {
    const until = holdUntilValue(choice, time);
    if (!until) {
      toast.error('Pick a time for the hold to end');
      return;
    }
    setBusy(true);
    try {
      await holdThermostat(thermostat.entity_id, setpoint, until);
      toast.success(`${name} is held`);
      onChanged();
    } catch (err) {
      toast.error(errorText(err, 'The hold did not work'));
    } finally {
      setBusy(false);
    }
  }

  async function resume() {
    setBusy(true);
    try {
      await resumeThermostat(thermostat.entity_id);
      toast.success(`${name} follows its schedule again`);
      onChanged();
    } catch (err) {
      toast.error(errorText(err, 'Resuming did not work'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3" data-testid="hold-control">
      <SetpointFields value={setpoint} onChange={setSetpoint} capabilities={thermostat.capabilities} idPrefix={`hold-${thermostat.entity_id}`} />
      <fieldset className="space-y-1">
        <legend className="mb-1 text-xs text-slate-400">How long</legend>
        {options.map((o) => (
          <label key={o.value} className={`flex min-h-11 items-center gap-2 text-sm ${o.disabled ? 'text-slate-600' : 'text-slate-200'}`}>
            <input type="radio" name={`until-${thermostat.entity_id}`} className="h-5 w-5" value={o.value} checked={choice === o.value}
              disabled={o.disabled} onChange={() => setChoice(o.value)} />
            {o.label}
          </label>
        ))}
        {choice === 'time' && (
          <Field label="Until">
            <input className={INPUT} type="time" value={time} onChange={(e) => setTime(e.target.value)} aria-label="Hold until" />
          </Field>
        )}
      </fieldset>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={PRIMARY} disabled={busy} onClick={() => void hold()}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Pause size={14} />} Hold
        </button>
        {thermostat.hold && (
          <button type="button" className={BUTTON} disabled={busy} onClick={() => void resume()}>
            <Play size={14} /> Resume schedule
          </button>
        )}
      </div>
    </div>
  );
}
