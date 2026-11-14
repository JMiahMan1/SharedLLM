import { useState, type ReactNode } from 'react';
import { ChevronDown, ChevronUp, Plus, Trash2 } from 'lucide-react';
import Toggle from '../ui/Toggle';
import {
  FIELD_SELECTORS, rowsToVariables, variableRows,
  type ScriptField,
} from '../../lib/automations';
import { Field } from './FormBits';
import { BUTTON, INPUT } from './styles';

/**
 * Parts of an automation or script that are not steps: its variables (set
 * before the actions run) and, for a script, the fields a caller passes in.
 * Both start folded, so a simple item stays a simple form.
 */

function Section({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  const [open, setOpen] = useState(count > 0);
  return (
    <div className="rounded-xl border border-white/10">
      <button type="button" className="flex min-h-11 w-full items-center justify-between px-3 text-left" aria-expanded={open}
        onClick={() => setOpen((v) => !v)}>
        <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">{title}{count ? ` (${count})` : ''}</span>
        {open ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
      </button>
      {open && <div className="space-y-2 border-t border-white/5 p-3">{children}</div>}
    </div>
  );
}

/** Variables as name/value rows; a value that reads as JSON (a number, a list, true) is kept as that. */
export function VariablesEditor({ value, disabled, onChange, title = 'Variables' }: {
  value: unknown; disabled: boolean; onChange: (next: Record<string, unknown> | undefined) => void; title?: string;
}) {
  const [rows, setRows] = useState(() => variableRows(value));
  const commit = (next: { key: string; value: string }[]) => {
    setRows(next);
    const vars = rowsToVariables(next);
    onChange(Object.keys(vars).length ? vars : undefined);
  };
  return (
    <Section title={title} count={rows.length}>
      {rows.length === 0 && <p className="text-xs text-slate-500">None. Templates in the steps can read these by name.</p>}
      {rows.map((row, i) => (
        <div key={i} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)_auto] items-end gap-2">
          <Field label="Name">
            <input className={INPUT} disabled={disabled} value={row.key}
              onChange={(e) => commit(rows.map((r, j) => (j === i ? { ...r, key: e.target.value } : r)))} />
          </Field>
          <Field label="Value">
            <input className={`${INPUT} font-mono text-xs`} disabled={disabled} value={row.value}
              onChange={(e) => commit(rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))} />
          </Field>
          {!disabled && (
            <button type="button" className="min-h-11 min-w-11 text-red-300" aria-label={`Remove variable ${row.key || i + 1}`}
              onClick={() => commit(rows.filter((_, j) => j !== i))}>
              <Trash2 size={16} className="mx-auto" />
            </button>
          )}
        </div>
      ))}
      {!disabled && (
        <button type="button" className={BUTTON} onClick={() => setRows([...rows, { key: '', value: '' }])}>
          <Plus size={14} /> Add variable
        </button>
      )}
    </Section>
  );
}

const selectorType = (field: ScriptField): string => Object.keys(field.selector ?? {})[0] ?? 'text';

/** A script's fields: what each is called, whether it must be given, its default and what kind of value it takes. */
export function ScriptFieldsEditor({ value, disabled, onChange }: {
  value: unknown; disabled: boolean; onChange: (next: Record<string, ScriptField> | undefined) => void;
}) {
  const fields = (value && typeof value === 'object' && !Array.isArray(value) ? value : {}) as Record<string, ScriptField>;
  const [keys, setKeys] = useState(() => Object.keys(fields));
  const [drafts, setDrafts] = useState<Record<string, ScriptField>>(fields);

  const commit = (nextKeys: string[], nextDrafts: Record<string, ScriptField>) => {
    setKeys(nextKeys);
    setDrafts(nextDrafts);
    const out: Record<string, ScriptField> = {};
    for (const k of nextKeys) if (k.trim()) out[k.trim()] = nextDrafts[k] ?? {};
    onChange(Object.keys(out).length ? out : undefined);
  };
  const setField = (key: string, patch: Partial<ScriptField>) => {
    const next = { ...(drafts[key] ?? {}), ...patch };
    for (const [k, v] of Object.entries(patch)) if (v === undefined || v === '' || v === false) delete (next as Record<string, unknown>)[k];
    commit(keys, { ...drafts, [key]: next });
  };
  const rename = (i: number, name: string) => {
    const old = keys[i];
    const nextDrafts = { ...drafts, [name]: drafts[old] ?? {} };
    if (name !== old) delete nextDrafts[old];
    commit(keys.map((k, j) => (j === i ? name : k)), nextDrafts);
  };

  return (
    <Section title="Fields a caller passes in" count={keys.length}>
      {keys.length === 0 && <p className="text-xs text-slate-500">None. A field becomes a variable of the same name.</p>}
      {keys.map((key, i) => {
        const f = drafts[key] ?? {};
        const type = selectorType(f);
        return (
          <div key={i} className="space-y-2 rounded-lg border border-white/5 p-2" data-testid="script-field">
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="Variable name">
                <input className={INPUT} disabled={disabled} value={key} onChange={(e) => rename(i, e.target.value)} />
              </Field>
              <Field label="Label">
                <input className={INPUT} disabled={disabled} value={f.name ?? ''} onChange={(e) => setField(key, { name: e.target.value })} />
              </Field>
              <Field label="Description">
                <input className={INPUT} disabled={disabled} value={f.description ?? ''} onChange={(e) => setField(key, { description: e.target.value })} />
              </Field>
              <Field label="Kind of value">
                <select className={INPUT} disabled={disabled} value={type}
                  onChange={(e) => setField(key, { selector: { [e.target.value]: e.target.value === 'select' ? { options: [] } : {} } })}>
                  {!FIELD_SELECTORS.includes(type) && <option value={type}>{type}</option>}
                  {FIELD_SELECTORS.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </Field>
              <Field label="Default (optional)">
                <input className={INPUT} disabled={disabled}
                  value={f.default === undefined ? '' : typeof f.default === 'object' ? JSON.stringify(f.default) : String(f.default)}
                  onChange={(e) => setField(key, { default: e.target.value === '' ? undefined : e.target.value })} />
              </Field>
              <div className="flex min-h-11 items-center justify-between gap-3 sm:self-end">
                <span className="text-xs text-slate-400">Must be given</span>
                <Toggle checked={f.required === true} disabled={disabled} ariaLabel={`${key || 'Field'} must be given`}
                  onChange={(v) => setField(key, { required: v || undefined })} />
              </div>
            </div>
            {!disabled && (
              <button type="button" className={BUTTON} aria-label={`Remove field ${key || i + 1}`}
                onClick={() => {
                  const nextDrafts = { ...drafts };
                  delete nextDrafts[key];
                  commit(keys.filter((_, j) => j !== i), nextDrafts);
                }}>
                <Trash2 size={14} /> Remove field
              </button>
            )}
          </div>
        );
      })}
      {!disabled && (
        <button type="button" className={BUTTON} onClick={() => {
          let n = keys.length + 1;
          while (keys.includes(`field_${n}`)) n += 1;
          commit([...keys, `field_${n}`], { ...drafts, [`field_${n}`]: { selector: { text: {} } } });
        }}>
          <Plus size={14} /> Add field
        </button>
      )}
    </Section>
  );
}
