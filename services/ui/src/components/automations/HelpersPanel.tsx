import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import {
  Calendar, Hash, ListChecks, Loader2, Minus, MousePointerClick, Pause, Pencil, Play, Plus, RotateCcw, Square,
  Timer, ToggleLeft, Type, X, type LucideIcon,
} from 'lucide-react';
import Modal from '../ui/Modal';
import Toggle from '../ui/Toggle';
import { confirmDialog } from '../ui/confirmDialog';
import { EmptyState, ErrorRetry } from '../ui/StateViews';
import { errorText } from '../../lib/errorText';
import {
  blankHelper, callHelper, cleanConfig, createHelper, deleteHelper, describeValue, editableConfig, listHelpers,
  updateHelper,
  type HelperDomain, type HelperField, type HelperRow, type HelperType,
} from '../../lib/helpers';
import { Chip, Field, MessageList } from './FormBits';
import { BUTTON, DANGER, INPUT, PRIMARY } from './styles';

const QUERY_KEY = ['homehub-helpers'];

const ICONS: Record<HelperDomain, LucideIcon> = {
  input_boolean: ToggleLeft, input_number: Hash, input_text: Type, input_select: ListChecks,
  input_datetime: Calendar, input_button: MousePointerClick, counter: Plus, timer: Timer,
};

/**
 * Admins: the hub's helpers (Home Assistant's toggles, numbers, texts,
 * dropdowns, dates, buttons, counters and timers). Each row shows the value
 * and the controls to change it; Edit and Add open a form built from the
 * hub's own description of each type, so nothing here is a guess about what
 * a helper takes.
 */
export default function HelpersPanel() {
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: QUERY_KEY, queryFn: listHelpers, retry: false,
    refetchInterval: () => (document.hidden ? false : 5_000) });
  const [editing, setEditing] = useState<{ type: HelperType; row: HelperRow | null } | null>(null);
  const [adding, setAdding] = useState(false);
  const types = list.data?.types ?? [];
  const refresh = () => void queryClient.invalidateQueries({ queryKey: QUERY_KEY });

  // The dialogs sit outside the panel: its backdrop filter would otherwise become the containing block of their
  // fixed layer, and a phone's bottom sheet would open inside the panel, under the tab bar.
  return (
    <>
    <section className="glass-panel space-y-3 rounded-2xl border border-white/10 p-3" aria-label="Helpers" data-testid="helpers-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-white">Helpers</h2>
          <p className="text-xs text-slate-400">Toggles, numbers, timers and counters your automations read and set.</p>
        </div>
        <button type="button" className={PRIMARY} disabled={!types.length} onClick={() => setAdding(true)}>
          <Plus size={14} aria-hidden /> Add helper
        </button>
      </div>
      <MessageList tone="warning" title="Left out (the hub could not read them)" items={list.data?.problems ?? []} />
      {list.isLoading ? (
        <div className="flex justify-center py-4" role="status" aria-label="Loading helpers"><Loader2 className="animate-spin text-slate-500" /></div>
      ) : list.isError ? (
        <ErrorRetry title={errorText(list.error, 'Could not reach the hub')} onRetry={() => void list.refetch()} />
      ) : (list.data?.helpers.length ?? 0) === 0 ? (
        <EmptyState title="No helpers yet" detail="Add one, or import them with your Home Assistant data." />
      ) : (
        <ul className="space-y-2" data-testid="helper-list">
          {list.data?.helpers.map((row) => (
            <HelperItem key={row.entity_id} row={row} onChanged={(next) => {
              queryClient.setQueryData(QUERY_KEY, (old: typeof list.data) => old && {
                ...old, helpers: old.helpers.map((h) => (h.entity_id === next.entity_id ? next : h)),
              });
            }}
              onEdit={() => {
                const type = types.find((t) => t.domain === row.domain);
                if (type) setEditing({ type, row });
              }} />
          ))}
        </ul>
      )}
    </section>
      {adding && (
        <Modal isOpen onClose={() => setAdding(false)} title="Add a helper" size="lg">
          <div className="grid gap-2 sm:grid-cols-2" data-testid="helper-types">
            {types.map((t) => {
              const Icon = ICONS[t.domain] ?? Plus;
              return (
                <button key={t.domain} type="button" className={`${BUTTON} justify-start py-2 text-sm`}
                  onClick={() => { setAdding(false); setEditing({ type: t, row: null }); }}>
                  <Icon size={16} aria-hidden /> {t.label}
                </button>
              );
            })}
          </div>
        </Modal>
      )}
      {editing && (
        <HelperForm type={editing.type} row={editing.row} onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); refresh(); }} />
      )}
    </>
  );
}

function HelperItem({ row, onEdit, onChanged }: { row: HelperRow; onEdit: () => void; onChanged: (r: HelperRow) => void }) {
  const Icon = ICONS[row.domain] ?? Plus;
  const run = useMutation({
    mutationFn: ({ service, data }: { service: string; data?: Record<string, unknown> }) => callHelper(row.domain, row.id, service, data),
    onSuccess: onChanged,
    onError: (err) => toast.error(errorText(err, 'That did not work')),
  });
  const call = (service: string, data?: Record<string, unknown>) => run.mutate({ service, data });
  return (
    <li className="rounded-xl border border-white/10 bg-white/[0.03] p-3" data-testid="helper-row">
      <div className="flex items-start gap-3">
        <Icon size={18} className="mt-0.5 shrink-0 text-sky-300" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-white">{row.name}</p>
          <p className="truncate text-[11px] text-slate-500">{row.entity_id}</p>
          <p className="text-xs text-slate-300" data-testid="helper-value">{describeValue(row)}</p>
        </div>
        <button type="button" className="min-h-11 min-w-11 text-slate-300" aria-label={`Edit ${row.name}`} onClick={onEdit}>
          <Pencil size={16} className="mx-auto" />
        </button>
      </div>
      {!row.disabled && row.state && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <HelperControls row={row} busy={run.isPending} call={call} />
        </div>
      )}
    </li>
  );
}

/** The controls a helper's own services give: switch, set, step, choose, press, start/pause/cancel. */
function HelperControls({ row, busy, call }: {
  row: HelperRow; busy: boolean; call: (service: string, data?: Record<string, unknown>) => void;
}) {
  const st = row.state!;
  const a = st.attributes;
  const [draft, setDraft] = useState('');
  const [duration, setDuration] = useState('');
  const name = row.name;
  switch (row.domain) {
    case 'input_boolean':
      return <Toggle checked={st.state === 'on'} disabled={busy} ariaLabel={`${name} on`} onChange={(v) => call(v ? 'turn_on' : 'turn_off')} />;
    case 'input_number':
    case 'counter':
      return (
        <>
          <button type="button" className={BUTTON} disabled={busy} aria-label={`${name} down`} onClick={() => call('decrement')}><Minus size={14} /></button>
          <button type="button" className={BUTTON} disabled={busy} aria-label={`${name} up`} onClick={() => call('increment')}><Plus size={14} /></button>
          <input className={`${INPUT} w-28`} type="number" inputMode="decimal" step="any" aria-label={`New value for ${name}`}
            min={typeof a.min === 'number' ? a.min : undefined} max={typeof a.max === 'number' ? a.max : undefined}
            value={draft} placeholder={st.state} onChange={(e) => setDraft(e.target.value)} />
          <button type="button" className={BUTTON} disabled={busy || draft === ''} onClick={() => { call('set_value', { value: Number(draft) }); setDraft(''); }}>Set</button>
          {row.domain === 'counter' && (
            <button type="button" className={BUTTON} disabled={busy} aria-label={`Reset ${name}`} onClick={() => call('reset')}><RotateCcw size={14} /> Reset</button>
          )}
        </>
      );
    case 'input_text':
      return (
        <>
          <input className={`${INPUT} min-w-0 flex-1`} aria-label={`New text for ${name}`} value={draft}
            type={a.mode === 'password' ? 'password' : 'text'}
            maxLength={typeof a.max === 'number' ? a.max : undefined} onChange={(e) => setDraft(e.target.value)} />
          <button type="button" className={BUTTON} disabled={busy} onClick={() => { call('set_value', { value: draft }); setDraft(''); }}>Set</button>
        </>
      );
    case 'input_select': {
      const options = Array.isArray(a.options) ? a.options.map(String) : [];
      return (
        <select className={`${INPUT} min-w-0 flex-1`} aria-label={`${name} option`} disabled={busy} value={st.state}
          onChange={(e) => call('select_option', { option: e.target.value })}>
          {!options.includes(st.state) && <option value={st.state}>{st.state}</option>}
          {options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      );
    }
    case 'input_datetime': {
      const hasDate = a.has_date === true;
      const hasTime = a.has_time === true;
      const type = hasDate && hasTime ? 'datetime-local' : hasDate ? 'date' : 'time';
      const current = hasDate && hasTime ? st.state.replace(' ', 'T') : st.state;
      return (
        <input className={`${INPUT} min-w-0 flex-1`} type={type} step={1} aria-label={`${name} value`} disabled={busy}
          value={current}
          onChange={(e) => {
            const v = e.target.value;
            if (!v) return;
            if (hasDate && hasTime) call('set_datetime', { datetime: v.replace('T', ' ') });
            else if (hasDate) call('set_datetime', { date: v });
            else call('set_datetime', { time: v });
          }} />
      );
    }
    case 'input_button':
      return <button type="button" className={BUTTON} disabled={busy} onClick={() => call('press')}><MousePointerClick size={14} /> Press</button>;
    case 'timer':
      return (
        <>
          {st.state !== 'active' && (
            <button type="button" className={BUTTON} disabled={busy} aria-label={`Start ${name}`}
              onClick={() => { call('start', duration ? { duration } : undefined); setDuration(''); }}>
              <Play size={14} /> {st.state === 'paused' ? 'Resume' : 'Start'}
            </button>
          )}
          {st.state === 'active' && (
            <button type="button" className={BUTTON} disabled={busy} aria-label={`Pause ${name}`} onClick={() => call('pause')}><Pause size={14} /> Pause</button>
          )}
          {st.state !== 'idle' && (
            <>
              <button type="button" className={BUTTON} disabled={busy} aria-label={`Finish ${name}`} onClick={() => call('finish')}><Square size={14} /> Finish</button>
              <button type="button" className={BUTTON} disabled={busy} aria-label={`Cancel ${name}`} onClick={() => call('cancel')}><X size={14} /> Cancel</button>
            </>
          )}
          {st.state === 'idle' && (
            <input className={`${INPUT} w-32`} aria-label={`Run ${name} for (HH:MM:SS)`} placeholder={String(a.duration ?? '0:00:00')}
              value={duration} onChange={(e) => setDuration(e.target.value)} />
          )}
        </>
      );
    default:
      return null;
  }
}

/** Add or edit a helper: its name, icon and the fields its type takes, as the hub describes them. */
function HelperForm({ type, row, onClose, onSaved }: { type: HelperType; row: HelperRow | null; onClose: () => void; onSaved: () => void }) {
  const [config, setConfig] = useState<Record<string, unknown>>(() => (row ? editableConfig(row) : blankHelper(type)));
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const set = (name: string, value: unknown) => setConfig((c) => ({ ...c, [name]: value }));

  async function save() {
    setBusy(true);
    setErrors([]);
    try {
      const body = cleanConfig(type, config);
      if (row) await updateHelper(row.domain, row.id, body);
      else await createHelper(type.domain, body);
      toast.success('Saved');
      onSaved();
    } catch (err) {
      setErrors([errorText(err, 'Saving did not work')]);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!row) return;
    const ok = await confirmDialog({
      title: `Delete ${row.name}?`,
      body: 'Automations that use it stop loading until it is back.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteHelper(row.domain, row.id);
      toast.success('Deleted');
      onSaved();
    } catch (err) {
      toast.error(errorText(err, 'Deleting did not work'));
    }
  }

  return (
    <Modal isOpen onClose={onClose} title={row ? `Edit ${row.name}` : `New ${type.label.toLowerCase()}`} size="lg">
      <div className="space-y-3" data-testid="helper-form">
        <MessageList tone="error" title="The hub found problems" items={errors} />
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name">
            <input className={INPUT} value={String(config.name ?? '')} onChange={(e) => set('name', e.target.value)} />
          </Field>
          <Field label="Icon (optional)" hint="Such as mdi:timer">
            <input className={INPUT} value={String(config.icon ?? '')} onChange={(e) => set('icon', e.target.value)} />
          </Field>
          {type.fields.map((f) => <HelperFieldInput key={f.name} field={f} value={config[f.name]} onChange={(v) => set(f.name, v)} />)}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/5 pt-3">
          {row ? <button type="button" className={DANGER} onClick={() => void remove()}>Delete</button> : <span />}
          <button type="button" className={PRIMARY} disabled={busy || !String(config.name ?? '').trim()} onClick={() => void save()}>
            {busy && <Loader2 size={14} className="animate-spin" />} Save
          </button>
        </div>
      </div>
    </Modal>
  );
}

function HelperFieldInput({ field, value, onChange }: { field: HelperField; value: unknown; onChange: (v: unknown) => void }) {
  const label = `${field.label}${field.required ? '' : ' (optional)'}`;
  if (field.kind === 'boolean') {
    return (
      <div className="flex min-h-11 items-center justify-between gap-3">
        <span className="text-xs text-slate-400">{field.label}</span>
        <Toggle checked={value === true} ariaLabel={field.label} onChange={(v) => onChange(v)} />
      </div>
    );
  }
  if (field.kind === 'select') {
    return (
      <Field label={label}>
        <select className={INPUT} value={String(value ?? field.default ?? '')} onChange={(e) => onChange(e.target.value)}>
          {(field.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </Field>
    );
  }
  if (field.kind === 'options') return <OptionsInput label={label} value={Array.isArray(value) ? value.map(String) : []} onChange={onChange} />;
  return (
    <Field label={label} hint={field.kind === 'duration' ? 'Hours, minutes and seconds' : undefined}>
      <input className={INPUT} type={field.kind === 'number' ? 'number' : 'text'} inputMode={field.kind === 'number' ? 'decimal' : undefined}
        step={field.kind === 'number' ? 'any' : undefined}
        value={value === undefined || value === null ? '' : String(value)} onChange={(e) => onChange(e.target.value)} />
    </Field>
  );
}

/** A dropdown's options: chips to remove, a box to add. */
function OptionsInput({ label, value, onChange }: { label: string; value: string[]; onChange: (v: string[]) => void }) {
  const [draft, setDraft] = useState('');
  const add = () => {
    const v = draft.trim();
    if (v && !value.includes(v)) onChange([...value, v]);
    setDraft('');
  };
  return (
    <div className="sm:col-span-2">
      <Field group label={label}>
        <div className="flex flex-wrap gap-1.5">
          {value.map((o) => (
            <Chip key={o} active label={`Remove option ${o}`} onClick={() => onChange(value.filter((x) => x !== o))}>{o} <X size={12} className="inline" /></Chip>
          ))}
        </div>
        <div className="mt-2 flex gap-2">
          <input className={`${INPUT} min-w-0 flex-1`} aria-label="New option" value={draft} onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }} />
          <button type="button" className={BUTTON} disabled={!draft.trim()} onClick={add}><Plus size={14} /> Add option</button>
        </div>
      </Field>
    </div>
  );
}
