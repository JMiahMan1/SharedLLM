import { useMemo } from 'react';
import {
  clockText, daySegments, formatTime, normaliseSlots, setpointText, slotTone, WEEKDAY_LABELS, WEEKDAYS,
  type Program,
} from '../../lib/schedules';

const TICKS = [0, 6, 12, 18, 24];

/**
 * A thermostat's week: one bar per day, Monday first, coloured by each slot's
 * mode with its temperature, and the program that covers the day beside it.
 * Each slot is a button (tap to edit, or to read it for a family member), so
 * nothing depends on hover or dragging.
 */
export default function WeekTimeline({ week, todayIndex, unit = '°', onSlot }: {
  /** For each day mon..sun, the program it follows (or null). */
  week: (Program | null)[];
  todayIndex?: number;
  unit?: string;
  onSlot?: (program: Program, slot: number, day: number) => void;
}) {
  const rows = useMemo(() => week.map((program) => {
    if (!program) return { program, slots: [], segments: [] };
    const slots = normaliseSlots(program.slots);
    return { program, slots, segments: daySegments(slots) };
  }), [week]);

  return (
    <div className="space-y-1" data-testid="week-timeline">
      <div className="ml-12 flex justify-between text-[10px] text-slate-500" aria-hidden>
        {TICKS.map((h) => <span key={h}>{h === 24 ? '' : clockText(formatTime(h * 60))}</span>)}
      </div>
      {rows.map(({ program, slots, segments }, day) => (
        <div key={WEEKDAYS[day]} className="flex items-center gap-2">
          <span className={`w-10 shrink-0 text-xs ${day === todayIndex ? 'font-bold text-white' : 'text-slate-400'}`}>
            {WEEKDAY_LABELS[WEEKDAYS[day]]}
          </span>
          <div className="relative h-11 flex-1 overflow-hidden rounded-lg bg-white/5" role="group"
            aria-label={`${WEEKDAY_LABELS[WEEKDAYS[day]]}: ${program ? program.name : 'no program'}`}>
            {!program && <span className="absolute inset-0 flex items-center justify-center text-[11px] text-slate-500">No program</span>}
            {program && segments.map((seg, i) => {
              const slot = slots[seg.slot];
              const text = setpointText(slot, unit);
              const label = `${WEEKDAY_LABELS[WEEKDAYS[day]]} ${clockText(formatTime(seg.start))} to ${clockText(formatTime(seg.end))}: ${text} (${program.name})`;
              return (
                <button
                  key={i}
                  type="button"
                  data-testid="timeline-slot"
                  className={`absolute top-0 h-full border-r border-black/30 px-1 text-left text-[10px] leading-tight ${slotTone(slot)} ${seg.carried ? 'opacity-70' : ''}`}
                  style={{ left: `${seg.left}%`, width: `${seg.width}%` }}
                  aria-label={label}
                  title={label}
                  disabled={!onSlot}
                  onClick={() => onSlot?.(program, seg.slot, day)}
                >
                  {seg.width > 8 && <span className="block truncate">{text}</span>}
                </button>
              );
            })}
          </div>
        </div>
      ))}
      <p className="ml-12 truncate text-[11px] text-slate-500">
        {[...new Set(week.filter(Boolean).map((p) => (p as Program).name))].join(' · ') || 'No program covers this week'}
      </p>
    </div>
  );
}
