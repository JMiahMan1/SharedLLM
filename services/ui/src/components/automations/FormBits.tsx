import { useMemo, useState, type ReactNode } from 'react';
import { X } from 'lucide-react';
import type { CatalogEntity } from '../../lib/automations';
import { BUTTON, INPUT } from './styles';

/**
 * Small form pieces shared by the Automations and Schedules editors. Every
 * control is at least 44px tall so it works under a thumb, and nothing relies
 * on hover.
 */

/**
 * A labelled control. `group` renders a div instead of a label element: a label
 * names every button inside it, so a picker full of buttons must not sit in one.
 */
export function Field({ label, hint, children, group = false }: { label: string; hint?: string; children: ReactNode; group?: boolean }) {
  const body = (
    <>
      <span className="mb-1 block">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-slate-500">{hint}</span>}
    </>
  );
  if (group) return <div className="block text-xs text-slate-400" role="group" aria-label={label}>{body}</div>;
  return <label className="block text-xs text-slate-400">{body}</label>;
}

/** A tappable on/off chip (weekdays, months, thermostats, people). */
export function Chip({ active, onClick, children, disabled, label }: {
  active: boolean; onClick: () => void; children: ReactNode; disabled?: boolean; label?: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className={`min-h-11 min-w-11 rounded-lg border px-3 text-xs disabled:opacity-60 ${
        active ? 'border-sky-400/60 bg-sky-500/20 text-sky-100' : 'border-white/10 text-slate-300 hover:bg-white/5'
      }`}
    >
      {children}
    </button>
  );
}

/** A list of errors or warnings from the hub, in its own words. */
export function MessageList({ tone, title, items }: { tone: 'error' | 'warning'; title?: string; items: string[] }) {
  if (!items.length) return null;
  const colour = tone === 'error' ? 'border-red-500/30 bg-red-500/10 text-red-200' : 'border-amber-500/30 bg-amber-500/10 text-amber-100';
  return (
    <div className={`rounded-lg border px-3 py-2 text-xs ${colour}`} role={tone === 'error' ? 'alert' : 'status'}>
      {title && <p className="mb-1 font-semibold">{title}</p>}
      <ul className="list-disc space-y-0.5 pl-4">
        {items.map((item, i) => <li key={i}>{item}</li>)}
      </ul>
    </div>
  );
}

/**
 * Pick one or several entities from a list the caller passes in (the hub's
 * catalog), searching by name or id. Matches show as a list of buttons under
 * the box, which works the same with a finger as with a mouse.
 */
export function EntityPicker({
  entities, value, onChange, multiple = false, domains, filter, placeholder = 'Search devices', disabled, label,
}: {
  entities: CatalogEntity[];
  value: string[];
  onChange: (next: string[]) => void;
  multiple?: boolean;
  /** Only these domains (e.g. ["light", "switch"]). */
  domains?: string[];
  filter?: (entity: CatalogEntity) => boolean;
  placeholder?: string;
  disabled?: boolean;
  label?: string;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const byId = useMemo(() => new Map(entities.map((e) => [e.entity_id, e])), [entities]);
  const matches = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    return entities
      .filter((e) => !domains?.length || domains.includes(e.domain || e.entity_id.split('.')[0]))
      .filter((e) => !filter || filter(e))
      .filter((e) => !value.includes(e.entity_id))
      .filter((e) => {
        if (!words.length) return true;
        const hay = `${e.name ?? ''} ${e.entity_id} ${e.area ?? ''}`.toLowerCase();
        return words.every((w) => hay.includes(w));
      })
      .slice(0, 8);
  }, [entities, domains, filter, value, query]);

  const pick = (id: string) => {
    onChange(multiple ? [...value, id] : [id]);
    setQuery('');
    setOpen(multiple);
  };

  return (
    <div className="space-y-1.5">
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {value.map((id) => (
            <span key={id} className="inline-flex min-h-9 items-center gap-1 rounded-lg border border-white/10 bg-white/5 pl-2.5 text-xs text-slate-200">
              <span className="max-w-[14rem] truncate">{byId.get(id)?.name || id}</span>
              {!disabled && (
                <button
                  type="button"
                  className="flex min-h-9 min-w-9 items-center justify-center text-slate-400 hover:text-white"
                  aria-label={`Remove ${byId.get(id)?.name || id}`}
                  onClick={() => onChange(value.filter((v) => v !== id))}
                >
                  <X size={14} />
                </button>
              )}
            </span>
          ))}
        </div>
      )}
      {!disabled && (multiple || value.length === 0 || open) && (
        <input
          className={INPUT}
          value={query}
          aria-label={label || placeholder}
          placeholder={placeholder}
          onFocus={() => setOpen(true)}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
        />
      )}
      {!disabled && !multiple && value.length > 0 && !open && (
        <button type="button" className={BUTTON} onClick={() => setOpen(true)}>Change</button>
      )}
      {!disabled && open && (
        <ul className="max-h-60 overflow-y-auto rounded-lg border border-white/10 bg-slate-900/95" aria-label={`Matches for ${label || placeholder}`}>
          {matches.length === 0 && <li className="px-3 py-2 text-xs text-slate-500">No matches</li>}
          {matches.map((e) => (
            <li key={e.entity_id}>
              <button
                type="button"
                className="flex min-h-11 w-full flex-col items-start justify-center px-3 py-1 text-left hover:bg-white/5"
                onClick={() => pick(e.entity_id)}
              >
                <span className="text-sm text-white">{e.name || e.entity_id}</span>
                <span className="text-[11px] text-slate-500">{e.entity_id}{e.area ? ` · ${e.area}` : ''}</span>
              </button>
            </li>
          ))}
          <li>
            <button type="button" className="min-h-11 w-full px-3 text-left text-xs text-slate-400" onClick={() => setOpen(false)}>
              Done
            </button>
          </li>
        </ul>
      )}
    </div>
  );
}

/** A JSON object edited as text; reports a parse error instead of saving junk. */
export function JsonBox({ value, onChange, label, rows = 4, disabled }: {
  value: unknown; onChange: (next: Record<string, unknown>) => void; label: string; rows?: number; disabled?: boolean;
}) {
  const [text, setText] = useState(() => JSON.stringify(value ?? {}, null, 2));
  const [error, setError] = useState('');
  return (
    <Field label={label}>
      <textarea
        className={`${INPUT} font-mono text-xs`}
        rows={rows}
        value={text}
        disabled={disabled}
        spellCheck={false}
        onChange={(e) => {
          setText(e.target.value);
          try {
            const parsed = e.target.value.trim() ? JSON.parse(e.target.value) : {};
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('not an object');
            setError('');
            onChange(parsed as Record<string, unknown>);
          } catch {
            setError('Not a JSON object yet; the last valid value is kept.');
          }
        }}
      />
      {error && <span className="mt-1 block text-[11px] text-amber-300">{error}</span>}
    </Field>
  );
}
