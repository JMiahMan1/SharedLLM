import { useEffect, useRef } from 'react';
import { applyLook, FILTERS, lookFor } from '../../lib/photoEdit/filters';
import type { Frame } from '../../lib/photoEdit/geometry';
import { NO_ADJUSTMENTS, type FilterId } from '../../lib/photoEdit/recipe';

function Thumb({ frame, filter }: { frame: Frame; filter: FilterId | null }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const data = new Uint8ClampedArray(frame.data);
    applyLook(data, frame.width, frame.height, lookFor(filter, NO_ADJUSTMENTS));
    canvas.width = frame.width;
    canvas.height = frame.height;
    ctx.putImageData(new ImageData(data, frame.width, frame.height), 0, 0);
    return () => {
      canvas.width = 0;
      canvas.height = 0;
    };
  }, [frame, filter]);
  return <canvas ref={ref} className="h-14 w-14 rounded-lg object-cover" aria-hidden />;
}

/** The one-tap looks with a live thumbnail of this picture in each; "Original" first. */
export default function FilterStrip({ thumb, value, onChange }: { thumb: Frame; value: FilterId | null; onChange: (id: FilterId | null) => void }) {
  const options: Array<{ id: FilterId | null; label: string }> = [{ id: null, label: 'Original' }, ...FILTERS.map((f) => ({ id: f.id, label: f.label }))];
  return (
    <div className="flex gap-2 overflow-x-auto px-3 pb-1" role="group" aria-label="Filters" data-testid="photo-editor-filters">
      {options.map((o) => (
        <button
          key={o.id ?? 'none'}
          type="button"
          aria-pressed={value === o.id}
          aria-label={`${o.label} filter`}
          onClick={() => onChange(o.id)}
          data-testid={`photo-editor-filter-${o.id ?? 'none'}`}
          className={`flex shrink-0 flex-col items-center gap-1 rounded-xl p-1 text-xs font-semibold ${value === o.id ? 'bg-white/20 text-white ring-2 ring-rose-400' : 'text-slate-300'}`}
        >
          <Thumb frame={thumb} filter={o.id} />
          <span aria-hidden>{o.label}</span>
        </button>
      ))}
    </div>
  );
}
