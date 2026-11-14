import { useState } from 'react';
import { ChevronDown, ChevronUp, Plus, Trash2 } from 'lucide-react';
import Toggle from '../ui/Toggle';
import { Chip, EntityPicker, Field, JsonBox } from './FormBits';
import { BUTTON, DANGER, INPUT } from './styles';
import {
  actionEntities, actionService, actionType, blankStep, conditionType, describeStep, forEachText, omit, parseForEach,
  REPEAT_LABELS, REPEAT_MODES, repeatMode, serviceFields, entityTakesService, serviceNames, servicesForEntities,
  triggerType, withActionEntities, withRepeatMode,
  type Catalog, type RepeatMode, type ServiceField, type Spec, type SpecField, type Step,
} from '../../lib/automations';
import { WEEKDAYS, WEEKDAY_LABELS } from '../../lib/schedules';

/**
 * The visual builder: a list of triggers, conditions or actions, each edited
 * with the fields the hub's catalog says its type takes. Nested steps (and/or/
 * not, choose, if/then/else, repeat, sequence, parallel, wait for a trigger)
 * are the same list again. A type the
 * builder has no form for is edited as a JSON object, so nothing in a config
 * is ever out of reach or silently dropped.
 */

export type Group = 'trigger' | 'condition' | 'action';

const GROUP_WORDS: Record<Group, { add: string; specs: keyof Pick<Catalog, 'triggers' | 'conditions' | 'actions'> }> = {
  trigger: { add: 'Add trigger', specs: 'triggers' },
  condition: { add: 'Add condition', specs: 'conditions' },
  action: { add: 'Add action', specs: 'actions' },
};

const RAW = 'raw';

function stepType(group: Group, step: Step): string {
  if (group === 'trigger') return triggerType(step);
  if (group === 'condition') return conditionType(step);
  return actionType(step);
}

function specFor(catalog: Catalog | undefined, group: Group, type: string): Spec | undefined {
  return catalog?.[GROUP_WORDS[group].specs]?.find((s) => s.type === type);
}

const asList = (value: unknown): string[] =>
  Array.isArray(value) ? value.map(String) : typeof value === 'string' && value ? [value] : [];

const asSteps = (value: unknown): Step[] =>
  Array.isArray(value) ? value.filter((v): v is Step => !!v && typeof v === 'object') : [];

export function StepList({ group, steps, onChange, catalog, disabled = false, label, depth = 0 }: {
  group: Group;
  steps: Step[];
  onChange: (next: Step[]) => void;
  catalog?: Catalog;
  disabled?: boolean;
  label?: string;
  depth?: number;
}) {
  const [adding, setAdding] = useState('');
  const specs = catalog?.[GROUP_WORDS[group].specs] ?? [];
  const set = (i: number, step: Step) => onChange(steps.map((s, j) => (j === i ? step : s)));
  const move = (i: number, by: number) => {
    const next = [...steps];
    const [row] = next.splice(i, 1);
    next.splice(i + by, 0, row);
    onChange(next);
  };

  return (
    <div className={`space-y-2 ${depth ? 'border-l border-white/10 pl-3' : ''}`}>
      {label && <p className="text-[10px] font-black uppercase tracking-widest text-slate-400">{label}</p>}
      {steps.length === 0 && <p className="text-xs text-slate-500">None yet.</p>}
      {steps.map((step, i) => (
        <StepCard
          key={i}
          group={group}
          step={step}
          catalog={catalog}
          disabled={disabled}
          depth={depth}
          onChange={(s) => set(i, s)}
          onRemove={() => onChange(steps.filter((_, j) => j !== i))}
          onUp={i > 0 ? () => move(i, -1) : undefined}
          onDown={i < steps.length - 1 ? () => move(i, 1) : undefined}
        />
      ))}
      {!disabled && (
        <div className="flex flex-wrap gap-2">
          <select
            className={`${INPUT} w-auto flex-1`}
            value={adding}
            aria-label={GROUP_WORDS[group].add}
            onChange={(e) => setAdding(e.target.value)}
          >
            <option value="">{GROUP_WORDS[group].add}...</option>
            {specs.map((s) => <option key={s.type} value={s.type}>{s.label}</option>)}
            <option value={RAW}>Custom step (JSON)</option>
          </select>
          <button
            type="button"
            className={BUTTON}
            disabled={!adding}
            onClick={() => {
              const step = adding === RAW ? {} : blankStep(group, adding, specFor(catalog, group, adding));
              onChange([...steps, step]);
              setAdding('');
            }}
          >
            <Plus size={14} /> Add
          </button>
        </div>
      )}
    </div>
  );
}

function StepCard({ group, step, catalog, disabled, depth, onChange, onRemove, onUp, onDown }: {
  group: Group; step: Step; catalog?: Catalog; disabled: boolean; depth: number;
  onChange: (s: Step) => void; onRemove: () => void; onUp?: () => void; onDown?: () => void;
}) {
  const [open, setOpen] = useState(true);
  const type = stepType(group, step);
  const spec = specFor(catalog, group, type);
  const title = describeStep(group, step, catalog);
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03]" data-testid={`step-${group}`}>
      <div className="flex items-center gap-1 px-2">
        <button
          type="button"
          className="flex min-h-11 min-w-0 flex-1 flex-col items-start justify-center px-1 text-left"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <span className="text-[10px] uppercase tracking-wider text-slate-500">{spec?.label || (type === RAW ? 'Custom step' : type)}</span>
          <span className="w-full truncate text-sm text-white">{title}</span>
        </button>
        {!disabled && (
          <>
            {onUp && <button type="button" className="min-h-11 min-w-11 text-slate-400" aria-label="Move up" onClick={onUp}><ChevronUp size={16} className="mx-auto" /></button>}
            {onDown && <button type="button" className="min-h-11 min-w-11 text-slate-400" aria-label="Move down" onClick={onDown}><ChevronDown size={16} className="mx-auto" /></button>}
            <button type="button" className="min-h-11 min-w-11 text-red-300" aria-label="Remove step" onClick={onRemove}><Trash2 size={16} className="mx-auto" /></button>
          </>
        )}
      </div>
      {open && (
        <div className="space-y-3 border-t border-white/5 p-3">
          <StepBody group={group} type={type} spec={spec} step={step} catalog={catalog} disabled={disabled} depth={depth} onChange={onChange} />
        </div>
      )}
    </div>
  );
}

function StepBody({ group, type, spec, step, catalog, disabled, depth, onChange }: {
  group: Group; type: string; spec?: Spec; step: Step; catalog?: Catalog; disabled: boolean; depth: number;
  onChange: (s: Step) => void;
}) {
  const nested = (key: string, g: Group, label?: string) => (
    <StepList group={g} steps={asSteps(step[key])} catalog={catalog} disabled={disabled} depth={depth + 1} label={label}
      onChange={(next) => onChange({ ...step, [key]: next })} />
  );

  if (group === 'condition' && (type === 'and' || type === 'or' || type === 'not')) return nested('conditions', 'condition');

  if (group === 'action') {
    if (type === 'service') return <ServiceAction step={step} catalog={catalog} disabled={disabled} onChange={onChange} />;
    if (type === 'condition') return <ConditionAction step={step} catalog={catalog} disabled={disabled} depth={depth} onChange={onChange} />;
    if (type === 'sequence') return nested('sequence', 'action', 'In order');
    if (type === 'parallel') return nested('parallel', 'action', 'All at the same time');
    if (type === 'wait_for_trigger') {
      return (
        <div className="space-y-3">
          {nested('wait_for_trigger', 'trigger', 'Until one of these happens')}
          {spec && <SpecFields spec={spec} step={step} catalog={catalog} disabled={disabled} onChange={onChange} />}
        </div>
      );
    }
    if (type === 'delay' && typeof step.delay !== 'object') {
      return (
        <Field label="Wait for (HH:MM:SS)">
          <input className={INPUT} value={String(step.delay ?? '')} disabled={disabled}
            onChange={(e) => onChange({ ...step, delay: e.target.value })} />
        </Field>
      );
    }
    if (type === 'if') {
      return (
        <div className="space-y-3">
          {nested('if', 'condition', 'If')}
          {nested('then', 'action', 'Then')}
          {nested('else', 'action', 'Else')}
        </div>
      );
    }
    if (type === 'choose') return <ChooseAction step={step} catalog={catalog} disabled={disabled} depth={depth} onChange={onChange} />;
    if (type === 'repeat' && step.repeat && typeof step.repeat === 'object') {
      return <RepeatAction step={step} catalog={catalog} disabled={disabled} depth={depth} onChange={onChange} />;
    }
  }

  if (type !== RAW && spec && spec.fields.length) {
    return <SpecFields spec={spec} step={step} catalog={catalog} disabled={disabled} onChange={onChange} />;
  }
  return <JsonBox label="This step as JSON" rows={6} value={step} disabled={disabled} onChange={onChange} />;
}

/** The catalog spec's fields for one step, each read from and written to `step[field.name]`. */
export function SpecFields({ spec, step, catalog, disabled, onChange }: {
  spec: Spec; step: Step; catalog?: Catalog; disabled: boolean; onChange: (s: Step) => void;
}) {
  const set = (name: string, value: unknown) => {
    if (value === undefined || value === '' || (Array.isArray(value) && !value.length)) onChange(omit(step, [name]));
    else onChange({ ...step, [name]: value });
  };
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {spec.fields.map((field) => (
        <SpecInput key={field.name} field={field} value={step[field.name]} catalog={catalog} disabled={disabled}
          onChange={(v) => set(field.name, v)} />
      ))}
    </div>
  );
}

/** repeat: a count, while or until conditions, or for each item of a list; then the steps to repeat. */
function RepeatAction({ step, catalog, disabled, depth, onChange }: {
  step: Step; catalog?: Catalog; disabled: boolean; depth: number; onChange: (s: Step) => void;
}) {
  const repeat = step.repeat as Step;
  const mode = repeatMode(repeat);
  const set = (next: Step) => onChange({ ...step, repeat: next });
  return (
    <div className="space-y-3">
      <Field label="Repeat">
        <select className={INPUT} disabled={disabled} value={mode}
          onChange={(e) => set(withRepeatMode(repeat, e.target.value as RepeatMode))}>
          {REPEAT_MODES.map((m) => <option key={m} value={m}>{REPEAT_LABELS[m]}</option>)}
        </select>
      </Field>
      {mode === 'count' && (
        <Field label="Times">
          <input className={INPUT} type="number" inputMode="numeric" value={String(repeat.count ?? '')} disabled={disabled}
            onChange={(e) => set({ ...repeat, count: Number(e.target.value) || 1 })} />
        </Field>
      )}
      {(mode === 'while' || mode === 'until') && (
        <StepList group="condition" steps={asSteps(repeat[mode])} catalog={catalog} disabled={disabled} depth={depth + 1}
          label={mode === 'while' ? 'While all of these hold' : 'Until all of these hold'}
          onChange={(next) => set({ ...repeat, [mode]: next })} />
      )}
      {mode === 'for_each' && (
        <ForEachInput value={repeat.for_each} disabled={disabled} onChange={(v) => set({ ...repeat, for_each: v })} />
      )}
      <StepList group="action" steps={asSteps(repeat.sequence)} catalog={catalog} disabled={disabled} depth={depth + 1} label="Repeat these"
        onChange={(next) => set({ ...repeat, sequence: next })} />
    </div>
  );
}

/** The items a for_each repeat walks, as typed text (kept as typed, so a new line can be started). */
function ForEachInput({ value, disabled, onChange }: { value: unknown; disabled: boolean; onChange: (v: unknown) => void }) {
  const [text, setText] = useState(() => forEachText(value));
  return (
    <Field label="Items" hint="One per line, a JSON list, or a template">
      <textarea className={`${INPUT} font-mono text-xs`} rows={3} spellCheck={false} disabled={disabled}
        value={text} onChange={(e) => { setText(e.target.value); onChange(parseForEach(e.target.value)); }} />
    </Field>
  );
}

/** A condition used as a step: pick its kind, then the same form as under "Only if". */
function ConditionAction({ step, catalog, disabled, depth, onChange }: {
  step: Step; catalog?: Catalog; disabled: boolean; depth: number; onChange: (s: Step) => void;
}) {
  const kind = conditionType(step);
  const specs = catalog?.conditions ?? [];
  return (
    <div className="space-y-3">
      <Field label="Continue only if">
        <select className={INPUT} disabled={disabled} value={kind}
          onChange={(e) => onChange(blankStep('condition', e.target.value, specFor(catalog, 'condition', e.target.value)))}>
          {!specs.some((s) => s.type === kind) && <option value={kind}>{kind}</option>}
          {specs.map((s) => <option key={s.type} value={s.type}>{s.label}</option>)}
        </select>
      </Field>
      <StepBody group="condition" type={kind} spec={specFor(catalog, 'condition', kind)} step={step} catalog={catalog}
        disabled={disabled} depth={depth} onChange={onChange} />
    </div>
  );
}

function domainsOf(field: SpecField): string[] | undefined {
  return field.domain ? field.domain.split(',').map((d) => d.trim()).filter(Boolean) : undefined;
}

function SpecInput({ field, value, catalog, disabled, onChange }: {
  field: SpecField; value: unknown; catalog?: Catalog; disabled: boolean; onChange: (v: unknown) => void;
}) {
  const label = `${field.label}${field.required ? '' : ' (optional)'}`;
  const entities = catalog?.entities ?? [];
  switch (field.kind) {
    case 'device': {
      const devices = catalog?.devices ?? [];
      const current = typeof value === 'string' ? value : '';
      return (
        <div className="sm:col-span-2">
          <Field label={label}>
            <select className={INPUT} disabled={disabled} value={current} onChange={(e) => onChange(e.target.value || undefined)}>
              <option value="">Choose a device</option>
              {current && !devices.some((d) => d.id === current) && <option value={current}>Device not on the hub ({current})</option>}
              {devices.map((d) => <option key={d.id} value={d.id}>{d.name}{d.area ? ` (${d.area})` : ''}</option>)}
            </select>
          </Field>
        </div>
      );
    }
    case 'conditions':
      // Edited where the step that owns it is drawn (repeat while/until).
      return null;
    case 'entity':
      return (
        <div className="sm:col-span-2">
          <Field group label={label}>
            <EntityPicker entities={entities} value={asList(value).slice(0, 1)} domains={domainsOf(field)} disabled={disabled}
              label={field.label} onChange={(next) => onChange(next[0])} />
          </Field>
        </div>
      );
    case 'entities':
      return (
        <div className="sm:col-span-2">
          <Field group label={label}>
            <EntityPicker entities={entities} value={asList(value)} multiple domains={domainsOf(field)} disabled={disabled}
              label={field.label} onChange={(next) => onChange(next.length === 1 ? next[0] : next)} />
          </Field>
        </div>
      );
    case 'number':
      return (
        <Field label={label}>
          <input className={INPUT} type="number" inputMode="decimal" step="any" disabled={disabled}
            value={value === undefined || value === null ? '' : String(value)}
            onChange={(e) => onChange(e.target.value === '' ? undefined : Number(e.target.value))} />
        </Field>
      );
    case 'time':
      return (
        <Field label={label}>
          <input className={INPUT} type="time" step={1} disabled={disabled}
            value={typeof value === 'string' ? value : ''}
            onChange={(e) => onChange(e.target.value.length === 5 ? `${e.target.value}:00` : e.target.value)} />
        </Field>
      );
    case 'select':
      return (
        <Field label={label}>
          <select className={INPUT} disabled={disabled} value={value === undefined ? '' : String(value)}
            onChange={(e) => onChange(e.target.value || undefined)}>
            {!field.required && <option value="">Any</option>}
            {(field.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </Field>
      );
    case 'template':
      return (
        <div className="sm:col-span-2">
          <Field label={label}>
            <textarea className={`${INPUT} font-mono text-xs`} rows={3} spellCheck={false} disabled={disabled}
              value={typeof value === 'string' ? value : ''} onChange={(e) => onChange(e.target.value)} />
          </Field>
        </div>
      );
    case 'weekdays': {
      const days = asList(value);
      return (
        <div className="sm:col-span-2">
          <p className="mb-1 text-xs text-slate-400">{label}</p>
          <div className="flex flex-wrap gap-1.5">
            {WEEKDAYS.map((d) => (
              <Chip key={d} active={days.includes(d)} disabled={disabled}
                onClick={() => onChange(days.includes(d) ? days.filter((x) => x !== d) : WEEKDAYS.filter((x) => x === d || days.includes(x)))}>
                {WEEKDAY_LABELS[d]}
              </Chip>
            ))}
          </div>
        </div>
      );
    }
    case 'boolean':
      return (
        <div className="flex min-h-11 items-center justify-between gap-3">
          <span className="text-xs text-slate-400">{label}</span>
          <Toggle checked={value === true} disabled={disabled} ariaLabel={field.label} onChange={(v) => onChange(v)} />
        </div>
      );
    case 'duration':
    case 'text':
    default:
      return (
        <Field label={label} hint={field.kind === 'duration' ? 'HH:MM:SS' : undefined}>
          <input className={INPUT} disabled={disabled}
            value={value === undefined || value === null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value)}
            onChange={(e) => onChange(e.target.value)} />
        </Field>
      );
  }
}

/** A service call: which service, on which entities, with which data (the catalog's fields for it). */
function ServiceAction({ step, catalog, disabled, onChange }: {
  step: Step; catalog?: Catalog; disabled: boolean; onChange: (s: Step) => void;
}) {
  const [asJson, setAsJson] = useState(false);
  const service = actionService(step);
  const chosen = actionEntities(step);
  // Only what the chosen devices can do (the hub's capability index), when it can tell.
  const supported = servicesForEntities(catalog, chosen);
  const names = supported ? serviceNames(catalog).filter((n) => supported.includes(n)) : serviceNames(catalog);
  const domain = service.split('.')[0];
  const fields = serviceFields(catalog, service);
  const data = (step.data && typeof step.data === 'object' ? step.data : {}) as Record<string, unknown>;
  // Generic domains act on any entity; others on their own domain.
  const domains = domain && !['homeassistant', 'script', 'scene', 'automation', 'notify'].includes(domain) ? [domain] : undefined;
  const setData = (next: Record<string, unknown>) => {
    const out = Object.keys(next).length ? { ...step, data: next } : omit(step, ['data']);
    onChange(out);
  };
  const setService = (value: string) => onChange({ ...omit(step, ['service']), action: value });

  return (
    <div className="space-y-3">
      <Field label="Service">
        {names.length ? (
          <select className={INPUT} disabled={disabled} value={service} onChange={(e) => setService(e.target.value)}>
            <option value="">Choose a service</option>
            {!names.includes(service) && service && <option value={service}>{service}</option>}
            {names.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        ) : (
          <input className={INPUT} disabled={disabled} value={service} placeholder="domain.service" onChange={(e) => setService(e.target.value)} />
        )}
      </Field>
      <Field group label="Devices">
        <EntityPicker
          entities={(catalog?.entities ?? []).filter((e) => !service || !domains || chosen.includes(e.entity_id)
            || entityTakesService(e, service))}
          value={chosen} multiple domains={domains} disabled={disabled}
          label="Devices" onChange={(next) => onChange(withActionEntities(step, next))} />
      </Field>
      {asJson ? (
        <JsonBox label="Data (JSON)" value={data} disabled={disabled} onChange={setData} />
      ) : (
        Object.entries(fields).filter(([name]) => name !== 'entity_id').length > 0 && (
          <div className="grid gap-3 sm:grid-cols-2">
            {Object.entries(fields).filter(([name]) => name !== 'entity_id').map(([name, f]) => (
              <ServiceDataInput key={name} name={name} field={f} value={data[name]} disabled={disabled}
                onChange={(v) => setData(v === undefined || v === '' ? omit(data, [name]) : { ...data, [name]: v })} />
            ))}
          </div>
        )
      )}
      <button type="button" className={BUTTON} onClick={() => setAsJson((v) => !v)}>
        {asJson ? 'Back to fields' : 'Edit data as JSON'}
      </button>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Keep the answer as (optional)" hint="A variable later steps can read">
          <input className={INPUT} disabled={disabled} value={typeof step.response_variable === 'string' ? step.response_variable : ''}
            placeholder="answer"
            onChange={(e) => onChange(e.target.value ? { ...step, response_variable: e.target.value } : omit(step, ['response_variable']))} />
        </Field>
        <div className="flex min-h-11 items-center justify-between gap-3 sm:self-end">
          <span className="text-xs text-slate-400">Carry on if it fails</span>
          <Toggle checked={step.continue_on_error === true} disabled={disabled} ariaLabel="Carry on if it fails"
            onChange={(v) => onChange(v ? { ...step, continue_on_error: true } : omit(step, ['continue_on_error']))} />
        </div>
      </div>
    </div>
  );
}

function ServiceDataInput({ name, field, value, disabled, onChange }: {
  name: string; field: ServiceField; value: unknown; disabled: boolean; onChange: (v: unknown) => void;
}) {
  const label = `${field.name || name}${field.required ? '' : ' (optional)'}`;
  const selector = field.selector ?? {};
  const hint = field.description;
  if ('boolean' in selector) {
    return (
      <div className="flex min-h-11 items-center justify-between gap-3">
        <span className="text-xs text-slate-400">{label}</span>
        <Toggle checked={value === true} disabled={disabled} ariaLabel={field.name || name} onChange={(v) => onChange(v)} />
      </div>
    );
  }
  const select = selector.select as { options?: (string | { value: string; label?: string })[] } | undefined;
  if (select?.options?.length) {
    return (
      <Field label={label} hint={hint}>
        <select className={INPUT} disabled={disabled} value={value === undefined ? '' : String(value)} onChange={(e) => onChange(e.target.value || undefined)}>
          <option value="">Leave as is</option>
          {select.options.map((o) => {
            const v = typeof o === 'string' ? o : o.value;
            return <option key={v} value={v}>{typeof o === 'string' ? o : o.label || o.value}</option>;
          })}
        </select>
      </Field>
    );
  }
  const numeric = 'number' in selector || typeof value === 'number';
  return (
    <Field label={label} hint={hint}>
      <input className={INPUT} disabled={disabled} type={numeric ? 'number' : 'text'} inputMode={numeric ? 'decimal' : undefined}
        step={numeric ? 'any' : undefined}
        placeholder={field.example !== undefined ? String(field.example) : undefined}
        value={value === undefined || value === null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value)}
        onChange={(e) => onChange(e.target.value === '' ? undefined : numeric ? Number(e.target.value) : e.target.value)} />
    </Field>
  );
}

/** choose: options, each with conditions and a sequence, then an optional default. */
function ChooseAction({ step, catalog, disabled, depth, onChange }: {
  step: Step; catalog?: Catalog; disabled: boolean; depth: number; onChange: (s: Step) => void;
}) {
  const options = asSteps(step.choose);
  const setOption = (i: number, option: Step) => onChange({ ...step, choose: options.map((o, j) => (j === i ? option : o)) });
  return (
    <div className="space-y-3">
      {options.map((option, i) => (
        <div key={i} className="space-y-2 rounded-lg border border-white/5 p-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-300">Option {i + 1}</span>
            {!disabled && (
              <button type="button" className={DANGER} onClick={() => onChange({ ...step, choose: options.filter((_, j) => j !== i) })}>
                Remove option
              </button>
            )}
          </div>
          <StepList group="condition" steps={asSteps(option.conditions)} catalog={catalog} disabled={disabled} depth={depth + 1}
            label="When" onChange={(next) => setOption(i, { ...option, conditions: next })} />
          <StepList group="action" steps={asSteps(option.sequence)} catalog={catalog} disabled={disabled} depth={depth + 1}
            label="Do" onChange={(next) => setOption(i, { ...option, sequence: next })} />
        </div>
      ))}
      {!disabled && (
        <button type="button" className={BUTTON} onClick={() => onChange({ ...step, choose: [...options, { conditions: [], sequence: [] }] })}>
          <Plus size={14} /> Add option
        </button>
      )}
      <StepList group="action" steps={asSteps(step.default)} catalog={catalog} disabled={disabled} depth={depth + 1}
        label="Otherwise" onChange={(next) => onChange(next.length ? { ...step, default: next } : omit(step, ['default']))} />
    </div>
  );
}
