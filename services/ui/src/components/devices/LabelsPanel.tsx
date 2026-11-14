import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Pencil, Plus, Tag, Trash2 } from 'lucide-react';
import { failureText } from '../../lib/discoveredDevices';
import { LABEL_COLORS, LABELS_KEY, createLabel, deleteLabel, getLabels, labelSwatch, updateLabel, type Label } from '../../lib/homeSetup';
import { confirmDialog } from '../ui/confirmDialog';
import SetupSection from './SetupSection';
import { BTN, DANGER, INPUT } from './setupStyles';

function ColorSelect({ value, label, onChange }: { value: string; label: string; onChange: (v: string) => void }) {
  return (
    <select className={`${INPUT} basis-32 sm:w-36`} value={value} aria-label={label} onChange={(e) => onChange(e.target.value)}>
      <option value="">No colour</option>
      {Object.keys(LABEL_COLORS).map((c) => <option key={c} value={c}>{c}</option>)}
      {value && !(value in LABEL_COLORS) && <option value={value}>{value}</option>}
    </select>
  );
}

function LabelRow({ label, onChanged }: { label: Label; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(label.name);
  const [color, setColor] = useState(label.color || '');
  const save = useMutation({
    mutationFn: () => updateLabel(label.label_id, { name: name.trim(), color: color || null }),
    onSuccess: () => { setEditing(false); onChanged(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const remove = useMutation({
    mutationFn: () => deleteLabel(label.label_id),
    onSuccess: () => { toast.success(`${label.name} was removed.`); onChanged(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const swatch = labelSwatch(label.color);
  if (editing) {
    return (
      <li className="px-4 py-3">
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) save.mutate(); }}>
          <input className={`${INPUT} flex-1 basis-40`} value={name} autoFocus aria-label={`New name for ${label.name}`}
            onChange={(e) => setName(e.target.value)} />
          <ColorSelect value={color} label={`Colour of ${label.name}`} onChange={setColor} />
          <button type="submit" className={BTN} disabled={save.isPending || !name.trim()}>Save</button>
          <button type="button" className={BTN} onClick={() => setEditing(false)}>Cancel</button>
        </form>
      </li>
    );
  }
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 px-4 py-2" data-testid="label">
      <span className="flex min-w-0 items-center gap-2 text-sm text-white">
        <span className="h-3 w-3 shrink-0 rounded-full border border-white/20" style={swatch ? { backgroundColor: swatch } : undefined} aria-hidden />
        <span className="truncate">{label.name}</span>
      </span>
      <span className="flex gap-2">
        <button type="button" className={BTN} onClick={() => setEditing(true)} aria-label={`Edit ${label.name}`}>
          <Pencil size={13} /> Edit
        </button>
        <button type="button" className={DANGER} disabled={remove.isPending} aria-label={`Remove ${label.name}`}
          onClick={async () => {
            if (await confirmDialog({ title: `Remove the label ${label.name}?`,
              body: 'It comes off every room, device and entity that wears it.', confirmLabel: 'Remove', danger: true })) {
              remove.mutate();
            }
          }}>
          <Trash2 size={13} />
        </button>
      </span>
    </li>
  );
}

/** Labels: made here, worn by rooms, devices and entities (set on each device's sheet). */
export default function LabelsPanel() {
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: LABELS_KEY, queryFn: getLabels, retry: false });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: LABELS_KEY });
    void queryClient.invalidateQueries({ queryKey: ['homehub-hardware'] });
  };
  const [name, setName] = useState('');
  const [color, setColor] = useState('');
  const add = useMutation({
    mutationFn: () => createLabel({ name: name.trim(), color: color || null }),
    onSuccess: (l) => { toast.success(`${l.name} was added.`); setName(''); setColor(''); refresh(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const labels = list.data ?? [];
  return (
    <SetupSection title="Labels" icon={Tag} testId="labels">
      {list.isLoading && <p className="p-4 text-sm text-slate-500">Loading labels…</p>}
      {list.error && <p className="p-4 text-sm text-amber-300/90" role="alert">{failureText(list.error)}</p>}
      {list.data && labels.length === 0 && (
        <p className="p-4 text-sm text-slate-500">No labels yet. A label groups devices across rooms, like "Night lights".</p>
      )}
      <ul className="divide-y divide-white/5">{labels.map((l) => <LabelRow key={l.label_id} label={l} onChanged={refresh} />)}</ul>
      <form className="flex flex-wrap items-end gap-2 border-t border-white/5 px-4 py-3"
        onSubmit={(e) => { e.preventDefault(); if (name.trim()) add.mutate(); }}>
        <input className={`${INPUT} flex-1 basis-40`} value={name} placeholder="New label" aria-label="New label name"
          onChange={(e) => setName(e.target.value)} />
        <ColorSelect value={color} label="Colour of the new label" onChange={setColor} />
        <button type="submit" className={BTN} disabled={add.isPending || !name.trim()}><Plus size={14} /> Add label</button>
      </form>
    </SetupSection>
  );
}
