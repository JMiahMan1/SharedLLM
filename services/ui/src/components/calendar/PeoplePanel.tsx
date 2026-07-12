import { useState } from 'react';
import { CALENDAR_PALETTE, calendarLabel, type CalendarPerson } from './calendarMeta';

let _pid = 0;
const genId = () => `p_${Date.now().toString(36)}_${(_pid++).toString(36)}`;
const nextColor = (people: CalendarPerson[]) => CALENDAR_PALETTE[people.length % CALENDAR_PALETTE.length];

interface Props {
  people: CalendarPerson[];
  discovered: string[];
  onSave: (people: CalendarPerson[]) => void;
  onClose?: () => void;
}

const PeoplePanel = ({ people, discovered, onSave, onClose }: Props) => {
  const [draft, setDraft] = useState<CalendarPerson[]>(people);

  const ownerOf = (account: string) => draft.find((p) => p.accounts.includes(account));

  const assignAccount = (account: string, value: string) => {
    setDraft((prev) => {
      let next = prev.map((p) => ({ ...p, accounts: p.accounts.filter((a) => a !== account) }));
      if (value === '__none__') return next;
      if (value === '__new__') {
        next = [...next, { id: genId(), name: calendarLabel(account), color: nextColor(next), accounts: [account] }];
      } else {
        next = next.map((p) => (p.id === value ? { ...p, accounts: [...p.accounts, account] } : p));
      }
      return next;
    });
  };

  const removeAccount = (personId: string, account: string) =>
    setDraft((prev) => prev.map((p) => (p.id === personId ? { ...p, accounts: p.accounts.filter((a) => a !== account) } : p)));

  const rename = (personId: string, name: string) =>
    setDraft((prev) => prev.map((p) => (p.id === personId ? { ...p, name } : p)));

  const setColor = (personId: string, color: string) =>
    setDraft((prev) => prev.map((p) => (p.id === personId ? { ...p, color } : p)));

  const deletePerson = (personId: string) => setDraft((prev) => prev.filter((p) => p.id !== personId));

  const mergePerson = (sourceId: string, targetId: string) =>
    setDraft((prev) => {
      const src = prev.find((p) => p.id === sourceId);
      if (!src) return prev;
      return prev
        .filter((p) => p.id !== sourceId)
        .map((p) => (p.id === targetId ? { ...p, accounts: [...new Set([...p.accounts, ...src.accounts])] } : p));
    });

  return (
    <div className="space-y-4">
      {/* Discovered calendars */}
      <div>
        <p className="mb-2 text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--os-ink-soft)' }}>
          Assign Calendars to People
        </p>
        {discovered.length === 0 && (
          <p className="text-[11px]" style={{ color: 'var(--os-ink-soft)' }}>No calendar accounts discovered yet.</p>
        )}
        <div className="space-y-2">
          {discovered.map((acc) => {
            const owner = ownerOf(acc);
            return (
              <div key={acc} className="flex items-center justify-between gap-2 rounded-xl border p-2" style={{ background: 'var(--os-panel-bg)', borderColor: 'var(--os-line)' }}>
                <div className="flex min-w-0 items-center gap-2">
                  <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: owner ? owner.color : '#a89f8d' }} />
                  <span className="truncate text-xs font-semibold" style={{ color: 'var(--os-ink)' }}>{calendarLabel(acc)}</span>
                  <span className="truncate text-[10px] opacity-60" style={{ color: 'var(--os-ink-soft)' }}>{acc}</span>
                </div>
                <select
                  aria-label={`Assign ${acc}`}
                  value={owner ? owner.id : '__none__'}
                  onChange={(e) => assignAccount(acc, e.target.value)}
                  className="shrink-0 rounded-lg border px-2 py-1 text-xs outline-none"
                  style={{ borderColor: 'var(--os-line)', background: 'var(--os-input-bg)', color: 'var(--os-ink)' }}
                >
                  <option value="__none__">Unassigned</option>
                  {draft.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                  <option value="__new__">+ New person</option>
                </select>
              </div>
            );
          })}
        </div>
      </div>

      {/* People */}
      <div className="border-t pt-3" style={{ borderColor: 'var(--os-line)' }}>
        <p className="mb-2 text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--os-ink-soft)' }}>People</p>
        <div className="space-y-3">
          {draft.map((p) => (
            <div key={p.id} className="rounded-xl border p-3" style={{ background: 'var(--os-panel-bg)', borderColor: 'var(--os-line)' }}>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  value={p.color}
                  onChange={(e) => setColor(p.id, e.target.value)}
                  className="h-7 w-7 shrink-0 cursor-pointer rounded border-0 bg-transparent p-0"
                  aria-label={`${p.name} color`}
                />
                <input
                  type="text"
                  value={p.name}
                  onChange={(e) => rename(p.id, e.target.value)}
                  className="flex-1 rounded-lg border px-2 py-1 text-sm font-bold outline-none"
                  style={{ borderColor: 'var(--os-line)', background: 'var(--os-input-bg)', color: 'var(--os-ink)' }}
                  placeholder="Person name"
                />
                <button onClick={() => deletePerson(p.id)} className="p-1" style={{ color: 'var(--os-ember-deep)' }} aria-label={`Delete ${p.name}`}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18M8 6V4h8v2m-9 0v14a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V6" /></svg>
                </button>
              </div>
              <div className="mt-2 flex flex-wrap gap-1">
                {p.accounts.map((a) => (
                  <span key={a} className="flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold" style={{ background: 'var(--os-paper-deep)', color: 'var(--os-ink-soft)' }}>
                    {calendarLabel(a)}
                    <button onClick={() => removeAccount(p.id, a)} className="opacity-60 hover:opacity-100" aria-label={`Remove ${a}`}>×</button>
                  </span>
                ))}
                {p.accounts.length === 0 && <span className="text-[10px] opacity-60" style={{ color: 'var(--os-ink-soft)' }}>no calendars</span>}
              </div>
              {draft.length > 1 && (
                <div className="mt-2 flex items-center gap-2">
                  <span className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--os-ink-soft)' }}>Merge into</span>
                  <select
                    aria-label={`Merge ${p.name} into`}
                    value=""
                    onChange={(e) => { if (e.target.value) mergePerson(p.id, e.target.value); }}
                    className="rounded-lg border px-2 py-1 text-xs outline-none"
                    style={{ borderColor: 'var(--os-line)', background: 'var(--os-input-bg)', color: 'var(--os-ink)' }}
                  >
                    <option value="">Select…</option>
                    {draft.filter((o) => o.id !== p.id).map((o) => (
                      <option key={o.id} value={o.id}>{o.name}</option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          ))}
          {draft.length === 0 && (
            <p className="text-[11px]" style={{ color: 'var(--os-ink-soft)' }}>No people yet. Use “+ New person” on a calendar above.</p>
          )}
        </div>
      </div>

      <div className="flex justify-end gap-2 border-t pt-3" style={{ borderColor: 'var(--os-line)' }}>
        {onClose && (
          <button onClick={onClose} className="rounded-xl px-4 py-2 text-xs font-bold" style={{ color: 'var(--os-ink-soft)' }}>Cancel</button>
        )}
        <button
          onClick={() => onSave(draft)}
          className="rounded-xl px-4 py-2 text-[10px] font-black uppercase tracking-widest text-[#fffdf8]"
          style={{ background: 'var(--os-ember)' }}
        >
          Save People
        </button>
      </div>
    </div>
  );
};

export default PeoplePanel;
