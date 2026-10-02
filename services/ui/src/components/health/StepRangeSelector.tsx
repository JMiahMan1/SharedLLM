import { useHaptics } from '../../hooks/useHaptics';
import { rangeOptions } from '../../lib/healthRanges';
import type { StepRange } from '../../types/api';

interface Props {
  value: StepRange;
  onChange: (range: StepRange) => void;
  narrow?: boolean;
}

/**
 * D/W/M/3M/Y selector.
 *
 * Every range stays available on a phone. Moving 3M and 3Y out to a submenu
 * would be tidier, but the data is the same for everyone and hiding it on a
 * small screen means the phone user sees less than the desktop user for no
 * reason other than width.
 */
export default function StepRangeSelector({ value, onChange, narrow = false }: Props) {
  const { trigger } = useHaptics();
  const options = rangeOptions(narrow);

  return (
    <div
      data-testid="step-range-selector"
      role="group"
      aria-label="Time range"
      className="inline-flex items-center gap-0.5 p-0.5 rounded-lg bg-white/5 border border-white/10"
    >
      {options.map((o) => {
        const active = o.id === value;
        return (
          <button
            key={o.id}
            onClick={() => {
              trigger('light');
              onChange(o.id);
            }}
            aria-pressed={active}
            data-testid={`range-${o.id}`}
            className={
              active
                ? 'px-2.5 py-1 rounded-md text-[11px] font-bold bg-purple-500/30 text-purple-100 border border-purple-400/40 pointer-coarse:min-h-11'
                : 'px-2.5 py-1 rounded-md text-[11px] font-semibold text-slate-400 hover:text-white hover:bg-white/5 pointer-coarse:min-h-11'
            }
          >
            <span aria-hidden="true">{o.short}</span>
            <span className="sr-only">{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}
