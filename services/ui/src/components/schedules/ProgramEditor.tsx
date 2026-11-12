import { useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { Loader2, Plus, Trash2 } from 'lucide-react';
import Modal from '../ui/Modal';
import Toggle from '../ui/Toggle';
import { confirmDialog } from '../ui/confirmDialog';
import { Chip, Field, MessageList } from '../automations/FormBits';
import { BUTTON, DANGER, INPUT, PRIMARY } from '../automations/styles';
import SetpointFields from './SetpointFields';
import { errorText } from '../../lib/errorText';
import { omit, validationErrors } from '../../lib/automations';
import {
  clockText, deleteProgram, formatTime, mergeCapabilities, normaliseSlots, parseTime, saveProgram, toggleWeekday,
  toTimeInput, WEEKDAY_GROUPS, WEEKDAY_LABELS, WEEKDAYS,
  type Program, type ProgramSlot, type Setpoint, type Thermostat,
} from '../../lib/schedules';

function slotSetpoint(slot: ProgramSlot): Setpoint {
  return omit(slot, ['start', 'stop']);
}

/** The weekday chips: the groups first, then each day. */
export function WeekdayChips({ value, onChange, disabled }: { value: string[]; onChange: (v: string[]) => void; disabled?: boolean }) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Days">
      {WEEKDAY_GROUPS.map((g) => (
        <Chip key={g} active={value.length === 1 && value[0] === g} disabled={disabled} onClick={() => onChange(toggleWeekday(value, g))}>
          {WEEKDAY_LABELS[g]}
        </Chip>
      ))}
      {WEEKDAYS.map((d) => (
        <Chip key={d} active={!WEEKDAY_GROUPS.includes(value[0] as typeof WEEKDAY_GROUPS[number]) && value.includes(d)}
          disabled={disabled} onClick={() => onChange(toggleWeekday(value, d))}>
          {WEEKDAY_LABELS[d]}
        </Chip>
      ))}
    </div>
  );
}

/**
 * A thermostat program: its name, the thermostats that follow it (several may
 * share one), the days and dates it covers, and its slots. A slot runs until
 * the next slot's start, so only starts are edited here; the hub fills stops.
 */
export default function ProgramEditor({ program, thermostats, canEdit, onClose, onSaved }: {
  program: Partial<Program>;
  thermostats: Thermostat[];
  canEdit: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<Partial<Program>>(() => ({ ...program, slots: normaliseSlots(program.slots ?? []) }));
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const readOnly = !canEdit || program.editable === false;
  const chosen = useMemo(() => thermostats.filter((t) => (draft.entity_ids ?? []).includes(t.entity_id)), [thermostats, draft.entity_ids]);
  const caps = mergeCapabilities(chosen.length ? chosen : thermostats);
  const slots = draft.slots ?? [];
  const setSlots = (next: ProgramSlot[]) => setDraft({ ...draft, slots: next });

  async function save() {
    if (!draft.name?.trim()) { setErrors(['Give the program a name.']); return; }
    if (!draft.entity_ids?.length) { setErrors(['Choose at least one thermostat.']); return; }
    if (!slots.length) { setErrors(['Add at least one slot.']); return; }
    setBusy(true);
    setErrors([]);
    try {
      await saveProgram(draft);
      toast.success('Program saved');
      onSaved();
    } catch (err) {
      setErrors([errorText(err, 'Saving did not work'), ...validationErrors(err)]);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!draft.id) return;
    if (!await confirmDialog({ title: 'Delete this program?', body: 'Its thermostats stop following it.', confirmLabel: 'Delete', danger: true })) return;
    try {
      await deleteProgram(draft.id);
      onSaved();
    } catch (err) {
      toast.error(errorText(err, 'Deleting did not work'));
    }
  }

  return (
    <Modal isOpen onClose={onClose} title={draft.id ? (readOnly ? 'Program' : 'Edit program') : 'New program'} size="2xl">
      <div className="space-y-4" data-testid="program-editor">
        {program.reason && <MessageList tone="warning" items={[program.reason]} />}
        <MessageList tone="error" items={errors} />
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name">
            <input className={INPUT} disabled={readOnly} value={draft.name ?? ''} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          </Field>
          <div className="flex min-h-11 items-end justify-between gap-3 pb-1">
            <span className="text-xs text-slate-400">On</span>
            <Toggle checked={draft.enabled !== false} disabled={readOnly} ariaLabel="Program on" onChange={(v) => setDraft({ ...draft, enabled: v })} />
          </div>
        </div>
        <div>
          <p className="mb-1 text-xs text-slate-400">Thermostats</p>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Thermostats">
            {thermostats.map((t) => {
              const on = (draft.entity_ids ?? []).includes(t.entity_id);
              return (
                <Chip key={t.entity_id} active={on} disabled={readOnly}
                  onClick={() => setDraft({ ...draft, entity_ids: on ? (draft.entity_ids ?? []).filter((e) => e !== t.entity_id) : [...(draft.entity_ids ?? []), t.entity_id] })}>
                  {t.name || t.entity_id}
                </Chip>
              );
            })}
          </div>
        </div>
        <div>
          <p className="mb-1 text-xs text-slate-400">Days</p>
          <WeekdayChips value={draft.weekdays ?? []} disabled={readOnly} onChange={(v) => setDraft({ ...draft, weekdays: v })} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="From (optional)">
            <input className={INPUT} type="date" disabled={readOnly} value={draft.start_date ?? ''} onChange={(e) => setDraft({ ...draft, start_date: e.target.value || null })} />
          </Field>
          <Field label="Until (optional)">
            <input className={INPUT} type="date" disabled={readOnly} value={draft.end_date ?? ''} onChange={(e) => setDraft({ ...draft, end_date: e.target.value || null })} />
          </Field>
        </div>

        <div className="space-y-2">
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-400">Slots</p>
          {slots.map((slot, i) => (
            <div key={i} className="space-y-2 rounded-xl border border-white/10 p-3" data-testid="program-slot">
              <div className="flex items-end gap-2">
                <Field label={`Starts${slot.stop ? ` (runs until ${clockText(slot.stop)})` : ''}`}>
                  <input className={INPUT} type="time" disabled={readOnly} value={toTimeInput(slot.start)} aria-label={`Slot ${i + 1} start`}
                    onChange={(e) => {
                      const at = parseTime(e.target.value);
                      if (at !== null) setSlots(slots.map((s, j) => (j === i ? { ...s, start: formatTime(at) } : s)));
                    }}
                    onBlur={() => setSlots(normaliseSlots(slots))} />
                </Field>
                {!readOnly && slots.length > 1 && (
                  <button type="button" className={DANGER} aria-label={`Remove slot ${i + 1}`} onClick={() => setSlots(normaliseSlots(slots.filter((_, j) => j !== i)))}>
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
              <SetpointFields value={slotSetpoint(slot)} capabilities={caps} disabled={readOnly} idPrefix={`slot-${i}`}
                onChange={(sp) => setSlots(slots.map((s, j) => (j === i ? { start: s.start, stop: s.stop, ...sp } : s)))} />
            </div>
          ))}
          {!readOnly && (
            <button type="button" className={BUTTON}
              onClick={() => {
                const last = slots[slots.length - 1];
                const at = last ? ((parseTime(last.start) ?? 0) + 60) % 1440 : 6 * 60;
                setSlots(normaliseSlots([...slots, { ...(last ? slotSetpoint(last) : { hvac_mode: caps.hvac_modes[0] ?? 'heat' }), start: formatTime(at) }]));
              }}>
              <Plus size={14} /> Add slot
            </button>
          )}
        </div>

        {!readOnly && (
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/5 pt-3">
            {draft.id ? <button type="button" className={DANGER} onClick={() => void remove()}><Trash2 size={14} /> Delete</button> : <span />}
            <button type="button" className={PRIMARY} disabled={busy} onClick={() => void save()}>
              {busy && <Loader2 size={14} className="animate-spin" />} Save program
            </button>
          </div>
        )}
      </div>
    </Modal>
  );
}

/**
 * One slot of a program, opened from the timeline. Moving its stop moves the
 * next slot's start, since a slot always runs until the next one begins.
 */
export function SlotEditor({ program, slot, thermostats, canEdit, onClose, onSaved, onEditProgram }: {
  program: Program;
  slot: number;
  thermostats: Thermostat[];
  canEdit: boolean;
  onClose: () => void;
  onSaved: () => void;
  onEditProgram: () => void;
}) {
  const [slots, setSlots] = useState(() => normaliseSlots(program.slots));
  const [busy, setBusy] = useState(false);
  const readOnly = !canEdit || program.editable === false;
  const current = slots[slot];
  const caps = mergeCapabilities(thermostats.filter((t) => program.entity_ids.includes(t.entity_id)));
  if (!current) return null;
  const next = (slot + 1) % slots.length;

  const setStart = (value: string) => {
    const at = parseTime(value);
    if (at !== null) setSlots(slots.map((s, j) => (j === slot ? { ...s, start: formatTime(at) } : s)));
  };
  const setStop = (value: string) => {
    const at = parseTime(value);
    if (at === null || slots.length < 2) return;
    setSlots(slots.map((s, j) => (j === slot ? { ...s, stop: formatTime(at) } : j === next ? { ...s, start: formatTime(at) } : s)));
  };

  async function save(remove = false) {
    setBusy(true);
    try {
      const out = remove ? slots.filter((_, j) => j !== slot) : slots;
      await saveProgram({ ...program, slots: normaliseSlots(out) });
      toast.success('Saved');
      onSaved();
    } catch (err) {
      toast.error(errorText(err, 'Saving did not work'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal isOpen onClose={onClose} title={`${program.name}: slot ${slot + 1}`} size="lg">
      <div className="space-y-3" data-testid="slot-editor">
        {program.reason && <MessageList tone="warning" items={[program.reason]} />}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Starts">
            <input className={INPUT} type="time" disabled={readOnly} value={toTimeInput(current.start)} onChange={(e) => setStart(e.target.value)} />
          </Field>
          <Field label="Ends" hint={slots.length > 1 ? 'Also when the next slot starts' : 'The only slot runs all day'}>
            <input className={INPUT} type="time" disabled={readOnly || slots.length < 2} value={toTimeInput(current.stop ?? slots[next].start)}
              onChange={(e) => setStop(e.target.value)} />
          </Field>
        </div>
        <SetpointFields value={slotSetpoint(current)} capabilities={caps} disabled={readOnly}
          onChange={(sp) => setSlots(slots.map((s, j) => (j === slot ? { start: s.start, stop: s.stop, ...sp } : s)))} />
        <div className="flex flex-wrap gap-2 border-t border-white/5 pt-3">
          {!readOnly && (
            <button type="button" className={PRIMARY} disabled={busy} onClick={() => void save()}>
              {busy && <Loader2 size={14} className="animate-spin" />} Save
            </button>
          )}
          {!readOnly && slots.length > 1 && (
            <button type="button" className={DANGER} disabled={busy} onClick={() => void save(true)}><Trash2 size={14} /> Remove slot</button>
          )}
          <button type="button" className={BUTTON} onClick={onEditProgram}>{readOnly ? 'Whole program' : 'Edit whole program'}</button>
        </div>
      </div>
    </Modal>
  );
}
