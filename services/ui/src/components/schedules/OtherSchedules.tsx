import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { AlertTriangle, CalendarClock, Loader2, Plus, Trash2 } from 'lucide-react';
import Modal from '../ui/Modal';
import Toggle from '../ui/Toggle';
import { confirmDialog } from '../ui/confirmDialog';
import { EntityPicker, Field, JsonBox, MessageList } from '../automations/FormBits';
import { BUTTON, DANGER, INPUT, PRIMARY } from '../automations/styles';
import { WeekdayChips } from './ProgramEditor';
import { errorText } from '../../lib/errorText';
import { serviceNames, validationErrors, type Catalog } from '../../lib/automations';
import {
  blankSchedule, blankTimeslot, deleteSchedule, formatSlotStart, listSchedules, momentText, parseSlotStart, saveSchedule,
  setScheduleEnabled, slotStartText, toTimeInput, weekdaysText,
  type Schedule, type ScheduleAction, type ScheduleCondition, type SlotStart, type Timeslot,
} from '../../lib/schedules';

const SCHEDULES_KEY = ['homehub-schedules'];

/**
 * Scheduler schedules that are not thermostat programs (lights at sunset, a
 * pump on weekday mornings): the list with an on/off switch and when each runs
 * next, and (admins) an editor.
 */
export default function OtherSchedules({ catalog }: { catalog?: Catalog }) {
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: SCHEDULES_KEY, queryFn: listSchedules, refetchInterval: () => (document.hidden ? false : 30_000) });
  const [editing, setEditing] = useState<Partial<Schedule> | null>(null);
  const canEdit = !!list.data?.can_edit;
  const schedules = (list.data?.schedules ?? []).filter((s) => !s.climate);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: SCHEDULES_KEY });
  const enable = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => setScheduleEnabled(id, enabled),
    onSuccess: refresh,
    onError: (err) => toast.error(errorText(err, 'That did not work')),
  });

  return (
    <div className="space-y-3" data-testid="other-schedules">
      {canEdit && (
        <button type="button" className={PRIMARY} onClick={() => setEditing(blankSchedule())}><Plus size={14} /> New schedule</button>
      )}
      {list.isLoading && <div className="flex justify-center py-8" role="status" aria-label="Loading"><Loader2 className="animate-spin text-slate-500" /></div>}
      {list.isError && <p className="text-sm text-red-300">{errorText(list.error, 'Could not reach the hub')}</p>}
      {list.data && schedules.length === 0 && <p className="glass-panel rounded-2xl p-6 text-center text-sm text-slate-400">No other schedules.</p>}
      <ul className="space-y-2">
        {schedules.map((s) => (
          <li key={s.schedule_id} className="glass-panel flex items-start gap-3 rounded-2xl border border-white/10 p-3" data-testid="schedule-row">
            <button type="button" className="min-h-11 min-w-0 flex-1 text-left" onClick={() => setEditing(s)} aria-label={`Open ${s.name || s.schedule_id}`}>
              <span className="block truncate text-sm font-semibold text-white">{s.name || s.schedule_id}</span>
              <span className="block text-xs text-slate-500">
                {weekdaysText(s.weekdays)} · {s.timeslots.map((t) => slotStartText(t.start)).join(', ')}
              </span>
              <span className="block text-xs text-slate-400">
                {s.enabled && s.next_trigger ? `Next: ${momentText(s.next_trigger)}` : s.enabled ? 'Nothing coming up' : 'Off'}
              </span>
              {s.unsupported_reason && (
                <span className="mt-1 inline-flex items-center gap-1 text-[11px] text-amber-200"><AlertTriangle size={11} aria-hidden />{s.unsupported_reason}</span>
              )}
              {s.last_error && <span className="block text-[11px] text-red-300">{s.last_error}</span>}
            </button>
            <div className="flex min-h-11 items-center">
              <Toggle checked={s.enabled} disabled={!canEdit} ariaLabel={`${s.name || s.schedule_id} on`}
                onChange={(v) => enable.mutate({ id: s.schedule_id, enabled: v })} />
            </div>
          </li>
        ))}
      </ul>
      {editing && (
        <ScheduleEditor schedule={editing} catalog={catalog} canEdit={canEdit}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); refresh(); }} />
      )}
    </div>
  );
}

const MATCH_TYPES: ScheduleCondition['match_type'][] = ['is', 'not', 'below', 'above'];

export function ScheduleEditor({ schedule, catalog, canEdit, onClose, onSaved }: {
  schedule: Partial<Schedule>;
  catalog?: Catalog;
  canEdit: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<Partial<Schedule>>(() => ({ ...blankSchedule(), ...schedule }));
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const readOnly = !canEdit;
  const slots = draft.timeslots ?? [];
  const setSlot = (i: number, patch: Partial<Timeslot>) => setDraft({ ...draft, timeslots: slots.map((s, j) => (j === i ? { ...s, ...patch } : s)) });

  async function save() {
    if (!slots.length || slots.some((s) => !s.start)) { setErrors(['Every timeslot needs a start.']); return; }
    setBusy(true);
    setErrors([]);
    try {
      await saveSchedule(draft);
      toast.success('Schedule saved');
      onSaved();
    } catch (err) {
      setErrors([errorText(err, 'Saving did not work'), ...validationErrors(err)]);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!draft.schedule_id) return;
    if (!await confirmDialog({ title: 'Delete this schedule?', body: 'It stops running.', confirmLabel: 'Delete', danger: true })) return;
    try {
      await deleteSchedule(draft.schedule_id);
      onSaved();
    } catch (err) {
      toast.error(errorText(err, 'Deleting did not work'));
    }
  }

  return (
    <Modal isOpen onClose={onClose} title={draft.schedule_id ? (readOnly ? 'Schedule' : 'Edit schedule') : 'New schedule'} size="2xl">
      <div className="space-y-4" data-testid="schedule-editor">
        {draft.unsupported_reason && <MessageList tone="warning" items={[draft.unsupported_reason]} />}
        <MessageList tone="error" items={errors} />
        <Field label="Name">
          <input className={INPUT} disabled={readOnly} value={draft.name ?? ''} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        </Field>
        <div>
          <p className="mb-1 text-xs text-slate-400">Days</p>
          <WeekdayChips value={draft.weekdays ?? []} disabled={readOnly} onChange={(v) => setDraft({ ...draft, weekdays: v })} />
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Field label="From (optional)">
            <input className={INPUT} type="date" disabled={readOnly} value={draft.start_date ?? ''} onChange={(e) => setDraft({ ...draft, start_date: e.target.value || null })} />
          </Field>
          <Field label="Until (optional)">
            <input className={INPUT} type="date" disabled={readOnly} value={draft.end_date ?? ''} onChange={(e) => setDraft({ ...draft, end_date: e.target.value || null })} />
          </Field>
          <Field label="After it runs">
            <select className={INPUT} disabled={readOnly} value={draft.repeat_type ?? 'repeat'}
              onChange={(e) => setDraft({ ...draft, repeat_type: e.target.value as Schedule['repeat_type'] })}>
              <option value="repeat">Keep repeating</option>
              <option value="pause">Turn itself off</option>
              <option value="single">Delete itself</option>
            </select>
          </Field>
        </div>

        {slots.map((slot, i) => (
          <TimeslotEditor key={i} index={i} slot={slot} catalog={catalog} disabled={readOnly}
            onChange={(patch) => setSlot(i, patch)}
            onRemove={slots.length > 1 ? () => setDraft({ ...draft, timeslots: slots.filter((_, j) => j !== i) }) : undefined} />
        ))}
        {!readOnly && (
          <button type="button" className={BUTTON} onClick={() => setDraft({ ...draft, timeslots: [...slots, blankTimeslot()] })}>
            <Plus size={14} /> Add timeslot
          </button>
        )}

        {!readOnly && (
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/5 pt-3">
            {draft.schedule_id ? <button type="button" className={DANGER} onClick={() => void remove()}><Trash2 size={14} /> Delete</button> : <span />}
            <button type="button" className={PRIMARY} disabled={busy} onClick={() => void save()}>
              {busy && <Loader2 size={14} className="animate-spin" />} Save schedule
            </button>
          </div>
        )}
      </div>
    </Modal>
  );
}

function StartInput({ value, onChange, disabled, label }: { value: string; onChange: (v: string) => void; disabled: boolean; label: string }) {
  const start = parseSlotStart(value);
  const set = (next: SlotStart) => onChange(formatSlotStart(next));
  return (
    <div className="grid grid-cols-2 gap-2">
      <Field label={label}>
        <select className={INPUT} disabled={disabled} value={start.kind}
          onChange={(e) => set(e.target.value === 'time' ? { kind: 'time', time: '08:00' } : { kind: e.target.value as 'sunrise' | 'sunset', offset: 0 })}>
          <option value="time">At a time</option>
          <option value="sunrise">Sunrise</option>
          <option value="sunset">Sunset</option>
        </select>
      </Field>
      {start.kind === 'time' ? (
        <Field label="Time">
          <input className={INPUT} type="time" disabled={disabled} value={start.time} onChange={(e) => set({ kind: 'time', time: e.target.value })} />
        </Field>
      ) : (
        <Field label="Offset (min, minus is before)">
          <input className={INPUT} type="number" inputMode="numeric" disabled={disabled} value={start.offset}
            onChange={(e) => set({ kind: start.kind, offset: Math.round(Number(e.target.value) || 0) })} />
        </Field>
      )}
    </div>
  );
}

function TimeslotEditor({ index, slot, catalog, disabled, onChange, onRemove }: {
  index: number; slot: Timeslot; catalog?: Catalog; disabled: boolean;
  onChange: (patch: Partial<Timeslot>) => void; onRemove?: () => void;
}) {
  const names = serviceNames(catalog);
  const entities = catalog?.entities ?? [];
  const actions = slot.actions ?? [];
  const conditions = slot.conditions ?? [];
  const setAction = (i: number, patch: Partial<ScheduleAction>) => onChange({ actions: actions.map((a, j) => (j === i ? { ...a, ...patch } : a)) });
  const setCondition = (i: number, patch: Partial<ScheduleCondition>) =>
    onChange({ conditions: conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)) });

  return (
    <div className="space-y-3 rounded-xl border border-white/10 p-3" data-testid="timeslot">
      <div className="flex items-center justify-between">
        <p className="flex items-center gap-1.5 text-xs font-semibold text-slate-300"><CalendarClock size={14} /> Timeslot {index + 1}</p>
        {!disabled && onRemove && <button type="button" className={DANGER} onClick={onRemove} aria-label={`Remove timeslot ${index + 1}`}><Trash2 size={14} /></button>}
      </div>
      <StartInput label="Starts" value={slot.start} disabled={disabled} onChange={(v) => onChange({ start: v })} />
      <Field label="Ends (optional, for actions that run over a period)">
        <input className={INPUT} type="time" disabled={disabled} value={toTimeInput(slot.stop)}
          onChange={(e) => onChange({ stop: e.target.value ? `${e.target.value}:00` : null })} />
      </Field>

      <p className="text-[10px] font-black uppercase tracking-widest text-slate-400">Do</p>
      {actions.map((a, i) => (
        <div key={i} className="space-y-2 rounded-lg border border-white/5 p-2">
          <div className="flex items-end gap-2">
            <Field label="Service">
              {names.length ? (
                <select className={INPUT} disabled={disabled} value={a.service} onChange={(e) => setAction(i, { service: e.target.value })}>
                  <option value="">Choose a service</option>
                  {a.service && !names.includes(a.service) && <option value={a.service}>{a.service}</option>}
                  {names.map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              ) : (
                <input className={INPUT} disabled={disabled} value={a.service} placeholder="domain.service" onChange={(e) => setAction(i, { service: e.target.value })} />
              )}
            </Field>
            {!disabled && (
              <button type="button" className={DANGER} aria-label={`Remove action ${i + 1}`} onClick={() => onChange({ actions: actions.filter((_, j) => j !== i) })}>
                <Trash2 size={14} />
              </button>
            )}
          </div>
          <Field group label="Device">
            <EntityPicker entities={entities} disabled={disabled} label="Device"
              domains={a.service && !a.service.startsWith('homeassistant.') ? [a.service.split('.')[0]] : undefined}
              value={a.entity_id ? [a.entity_id] : []}
              onChange={(next) => setAction(i, { entity_id: next[0] })} />
          </Field>
          <JsonBox label="Data (JSON)" rows={2} value={a.service_data ?? {}} disabled={disabled}
            onChange={(v) => setAction(i, { service_data: v })} />
        </div>
      ))}
      {!disabled && (
        <button type="button" className={BUTTON} onClick={() => onChange({ actions: [...actions, { service: '', service_data: {} }] })}>
          <Plus size={14} /> Add action
        </button>
      )}

      <p className="text-[10px] font-black uppercase tracking-widest text-slate-400">Only if</p>
      {conditions.length > 1 && (
        <Field label="Match">
          <select className={INPUT} disabled={disabled} value={slot.condition_type ?? 'and'}
            onChange={(e) => onChange({ condition_type: e.target.value as 'and' | 'or' })}>
            <option value="and">All of these</option>
            <option value="or">Any of these</option>
          </select>
        </Field>
      )}
      {conditions.map((c, i) => (
        <div key={i} className="space-y-2 rounded-lg border border-white/5 p-2">
          <EntityPicker entities={entities} disabled={disabled} label="Condition device"
            value={c.entity_id ? [c.entity_id] : []} onChange={(next) => setCondition(i, { entity_id: next[0] ?? '' })} />
          <div className="flex items-end gap-2">
            <Field label="Is">
              <select className={INPUT} disabled={disabled} value={c.match_type} onChange={(e) => setCondition(i, { match_type: e.target.value as ScheduleCondition['match_type'] })}>
                {MATCH_TYPES.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </Field>
            <Field label="Value">
              <input className={INPUT} disabled={disabled} value={String(c.value ?? '')}
                onChange={(e) => setCondition(i, { value: c.match_type === 'below' || c.match_type === 'above' ? Number(e.target.value) : e.target.value })} />
            </Field>
            {!disabled && (
              <button type="button" className={DANGER} aria-label={`Remove condition ${i + 1}`} onClick={() => onChange({ conditions: conditions.filter((_, j) => j !== i) })}>
                <Trash2 size={14} />
              </button>
            )}
          </div>
        </div>
      ))}
      {conditions.length > 0 && (
        <div className="flex min-h-11 items-center justify-between gap-3">
          <span className="text-xs text-slate-400">Act again when the conditions become true during the slot</span>
          <Toggle checked={slot.track_conditions} disabled={disabled} ariaLabel="Track conditions" onChange={(v) => onChange({ track_conditions: v })} />
        </div>
      )}
      {!disabled && (
        <button type="button" className={BUTTON} onClick={() => onChange({ conditions: [...conditions, { entity_id: '', match_type: 'is', value: 'on' }] })}>
          <Plus size={14} /> Add condition
        </button>
      )}
    </div>
  );
}
