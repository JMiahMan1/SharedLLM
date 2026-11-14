import { Check } from 'lucide-react';
import { labelSwatch, toggled, type Label } from '../../lib/homeSetup';

/**
 * The hub's labels as chips a person taps on or off (a device, an entity or an area). Every label is shown;
 * the ones worn are filled. ``name`` says what is being labelled, for screen readers.
 */
export default function LabelChips({ labels, selected, name, disabled, onChange }: {
  labels: Label[]; selected: string[]; name: string; disabled?: boolean; onChange: (next: string[]) => void;
}) {
  if (labels.length === 0) {
    return <p className="text-xs text-slate-500">No labels yet. Make them under Devices, Rooms &amp; places.</p>;
  }
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label={`Labels of ${name}`} data-testid="label-chips">
      {labels.map((l) => {
        const on = selected.includes(l.label_id);
        const swatch = labelSwatch(l.color);
        return (
          <button key={l.label_id} type="button" aria-pressed={on} disabled={disabled}
            onClick={() => onChange(toggled(selected, l.label_id))}
            className={`inline-flex min-h-11 max-w-full items-center gap-1.5 rounded-full border px-3 text-xs disabled:opacity-50 ${
              on ? 'border-sky-400/60 bg-sky-500/15 text-sky-100' : 'border-white/10 text-slate-300 hover:bg-white/5'}`}>
            {swatch && <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: swatch }} aria-hidden />}
            <span className="min-w-0 truncate">{l.name}</span>
            {on && <Check size={12} className="shrink-0" aria-hidden />}
          </button>
        );
      })}
    </div>
  );
}
