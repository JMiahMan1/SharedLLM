import { duration } from '../../lib/phoneUsageFormat';
import type { UsageDay } from '../../types/api';

function clock(epochMs: number | null | undefined): string {
  if (!epochMs) return '—';
  return new Date(epochMs).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

/** A day of phone use in four numbers: screen time, first and last use, sessions (the Health hub and the family member page). */
export default function UsageTiles({ day }: { day: UsageDay }) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {[
        { label: 'Screen time', value: duration(day.millis) },
        { label: 'First use', value: clock(day.first_use) },
        { label: 'Last use', value: clock(day.last_use) },
        { label: 'Sessions', value: String(day.sessions ?? 0) },
      ].map((tile) => (
        <div key={tile.label} className="rounded-xl border border-white/10 bg-white/5 px-3 py-2">
          <p className="text-[10px] uppercase tracking-wider text-slate-500">{tile.label}</p>
          <p className="text-sm font-semibold text-slate-100" data-testid={`wellbeing-${tile.label.toLowerCase().replace(' ', '-')}`}>{tile.value}</p>
        </div>
      ))}
    </div>
  );
}
