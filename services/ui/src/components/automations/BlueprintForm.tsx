import { useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import Toggle from '../ui/Toggle';
import { errorText } from '../../lib/errorText';
import {
  entitySelector, getBlueprint, selectOptions, selectorKind, withBlueprintInput, blueprintUse,
  type BlueprintInput, type Catalog, type Config, type Step,
} from '../../lib/automations';
import { EntityPicker, Field, JsonBox, MessageList } from './FormBits';
import { StepList } from './StepEditor';
import { INPUT } from './styles';

/**
 * An automation or script that uses a blueprint: the blueprint supplies its
 * triggers, conditions and actions, so the form only asks for the inputs the
 * blueprint declares, each with the control its selector calls for. Nothing
 * here writes steps (the hub refuses them next to `use_blueprint`); an input
 * left empty uses the blueprint's default.
 */
export default function BlueprintForm({ config, catalog, disabled, onChange }: {
  config: Config; catalog?: Catalog; disabled: boolean; onChange: (next: Config) => void;
}) {
  const use = blueprintUse(config);
  const path = use?.path ?? '';
  const blueprint = useQuery({ queryKey: ['homehub-blueprint', path], queryFn: () => getBlueprint(path), enabled: !!path, retry: false });
  if (!use) return null;

  const sections: { title: string | null; inputs: BlueprintInput[] }[] = [];
  for (const input of blueprint.data?.inputs ?? []) {
    const last = sections[sections.length - 1];
    if (last && last.title === input.section) last.inputs.push(input);
    else sections.push({ title: input.section, inputs: [input] });
  }

  return (
    <div className="space-y-3 rounded-xl border border-sky-500/20 bg-sky-500/5 p-3" data-testid="blueprint-form">
      <div className="min-w-0">
        <p className="text-[10px] font-black uppercase tracking-widest text-slate-400">Blueprint</p>
        <p className="break-words text-sm font-semibold text-white">{blueprint.data?.name || path}</p>
        {blueprint.data?.description && <p className="mt-1 break-words text-xs text-slate-400">{blueprint.data.description}</p>}
        <p className="mt-1 text-[11px] text-slate-500">
          Its triggers, conditions and actions come from the blueprint. Inputs left empty use the blueprint's default.
        </p>
      </div>
      {blueprint.isLoading && <div role="status" aria-label="Loading the blueprint" className="flex justify-center py-3"><Loader2 className="animate-spin text-slate-500" /></div>}
      {blueprint.isError && (
        <>
          <MessageList tone="error" items={[errorText(blueprint.error, `The blueprint ${path} could not be loaded`)]} />
          <JsonBox label="Inputs (JSON)" value={use.input} disabled={disabled}
            onChange={(v) => onChange({ ...config, use_blueprint: { ...(config.use_blueprint as Record<string, unknown>), input: v } })} />
        </>
      )}
      {sections.map((section, i) => (
        <div key={`${section.title ?? ''}-${i}`} className="space-y-3">
          {section.title && <p className="text-xs font-semibold text-slate-300">{section.title}</p>}
          {section.inputs.map((input) => (
            <BlueprintInputField key={input.name} input={input} value={use.input[input.name]} catalog={catalog} disabled={disabled}
              onChange={(v) => onChange(withBlueprintInput(config, input.name, v))} />
          ))}
        </div>
      ))}
      {blueprint.data && blueprint.data.inputs.length === 0 && <p className="text-xs text-slate-500">This blueprint takes no inputs.</p>}
    </div>
  );
}

function defaultText(input: BlueprintInput): string | undefined {
  if (input.default === undefined || input.default === null || input.default === '') return undefined;
  return typeof input.default === 'object' ? JSON.stringify(input.default) : String(input.default);
}

export function BlueprintInputField({ input, value, catalog, disabled, onChange }: {
  input: BlueprintInput; value: unknown; catalog?: Catalog; disabled: boolean; onChange: (v: unknown) => void;
}) {
  const label = `${input.label}${input.required ? '' : ' (optional)'}`;
  const hint = [input.description, defaultText(input) !== undefined ? `Default: ${defaultText(input)}` : '']
    .filter(Boolean).join(' ') || undefined;
  const kind = selectorKind(input.selector);
  const asList = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : typeof v === 'string' && v ? [v] : []);
  const asSteps = (v: unknown): Step[] => (Array.isArray(v) ? v.filter((x): x is Step => !!x && typeof x === 'object') : []);

  switch (kind) {
    case 'entity': {
      const { domains, multiple } = entitySelector(input.selector);
      const target = 'target' in input.selector;
      const current = target && value && typeof value === 'object' && !Array.isArray(value)
        ? asList((value as Record<string, unknown>).entity_id) : asList(value);
      return (
        <Field group label={label} hint={hint}>
          <EntityPicker entities={catalog?.entities ?? []} value={current} multiple={multiple} domains={domains} disabled={disabled}
            label={input.label}
            onChange={(next) => {
              const picked = multiple ? next : next[0];
              onChange(target ? (next.length ? { entity_id: picked } : undefined) : picked);
            }} />
        </Field>
      );
    }
    case 'device': {
      const devices = catalog?.devices ?? [];
      return (
        <Field label={label} hint={hint}>
          <select className={INPUT} disabled={disabled} value={typeof value === 'string' ? value : ''} onChange={(e) => onChange(e.target.value || undefined)}>
            <option value="">{input.required ? 'Choose a device' : 'Use the default'}</option>
            {devices.map((d) => <option key={d.id} value={d.id}>{d.name}{d.area ? ` (${d.area})` : ''}</option>)}
          </select>
        </Field>
      );
    }
    case 'boolean':
      return (
        <div className="flex min-h-11 items-center justify-between gap-3">
          <span className="text-xs text-slate-400">{label}{hint ? <span className="block text-[11px] text-slate-500">{hint}</span> : null}</span>
          <Toggle checked={value === undefined ? input.default === true : value === true} disabled={disabled} ariaLabel={input.label}
            onChange={(v) => onChange(v)} />
        </div>
      );
    case 'number': {
      const n = (input.selector.number ?? {}) as Record<string, unknown>;
      return (
        <Field label={label} hint={hint}>
          <input className={INPUT} type="number" inputMode="decimal" disabled={disabled}
            min={typeof n.min === 'number' ? n.min : undefined} max={typeof n.max === 'number' ? n.max : undefined}
            step={typeof n.step === 'number' ? n.step : 'any'}
            value={value === undefined || value === null ? '' : String(value)}
            onChange={(e) => onChange(e.target.value === '' ? undefined : Number(e.target.value))} />
        </Field>
      );
    }
    case 'select': {
      const options = selectOptions(input.selector);
      return (
        <Field label={label} hint={hint}>
          <select className={INPUT} disabled={disabled} value={value === undefined ? '' : String(value)} onChange={(e) => onChange(e.target.value || undefined)}>
            <option value="">{input.required ? 'Choose' : 'Use the default'}</option>
            {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Field>
      );
    }
    case 'time':
      return (
        <Field label={label} hint={hint}>
          <input className={INPUT} type="time" step={1} disabled={disabled} value={typeof value === 'string' ? value : ''}
            onChange={(e) => onChange(e.target.value ? (e.target.value.length === 5 ? `${e.target.value}:00` : e.target.value) : undefined)} />
        </Field>
      );
    case 'action':
    case 'condition':
    case 'trigger': {
      const group = kind;
      return (
        <StepList group={group} steps={asSteps(value)} catalog={catalog} disabled={disabled} depth={1} label={label}
          onChange={(next) => onChange(next.length ? next : undefined)} />
      );
    }
    case 'object':
      return <JsonBox label={`${label} (JSON)`} value={value ?? {}} disabled={disabled} onChange={(v) => onChange(Object.keys(v).length ? v : undefined)} />;
    case 'template':
      return (
        <Field label={label} hint={hint}>
          <textarea className={`${INPUT} font-mono text-xs`} rows={3} spellCheck={false} disabled={disabled}
            value={typeof value === 'string' ? value : ''} onChange={(e) => onChange(e.target.value || undefined)} />
        </Field>
      );
    case 'duration':
    case 'text':
    default:
      return (
        <Field label={label} hint={kind === 'duration' ? [hint, 'HH:MM:SS'].filter(Boolean).join(' ') : hint}>
          <input className={INPUT} disabled={disabled}
            value={value === undefined || value === null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value)}
            onChange={(e) => onChange(e.target.value || undefined)} />
        </Field>
      );
  }
}
