import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Compass } from 'lucide-react';
import { api } from '../../services/api';

const WINDOWS = [7, 30] as const;

/**
 * What you do most inside Jarvis (Phase 10).
 *
 * The web bundle records one light event per page and the recorders add plays,
 * wins and chapters; this shows the person their own small picture - the
 * features they use most - from their own feed. It is the member's own
 * derived summary, never the raw stream (that stays the parents'): no times,
 * no items, no one else. Nothing recorded yet is said plainly.
 */
export default function JarvisUseCard() {
  const [days, setDays] = useState<number>(WINDOWS[0]);
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['jarvis-use', days],
    queryFn: () => api.getJarvisUse(days),
    staleTime: 60_000,
  });

  const use = data?.use;
  const top = use?.top ?? [];
  const busiest = Math.max(1, ...top.map((row) => row.count));

  return (
    <section className="glass-panel rounded-2xl border border-white/5 p-4" aria-labelledby="jarvis-use-heading" data-testid="jarvis-use-card">
      <div className="flex items-center justify-between gap-2">
        <h3 id="jarvis-use-heading" className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
          <Compass size={13} className="text-purple-300" />
          What you do in Jarvis
        </h3>
        <span className="flex gap-1" role="group" aria-label="Over the last">
          {WINDOWS.map((window) => (
            <button
              key={window}
              type="button"
              aria-pressed={days === window}
              onClick={() => setDays(window)}
              className={`min-h-9 shrink-0 rounded-lg border px-2.5 text-[11px] ${
                days === window ? 'border-purple-400/40 bg-purple-400/10 text-white' : 'border-white/10 bg-white/[0.03] text-slate-400'
              }`}
            >
              {window} d
            </button>
          ))}
        </span>
      </div>

      {isLoading ? (
        <div className="mt-3 space-y-2" aria-hidden>
          <div className="h-3 w-40 skeleton rounded" />
          <div className="h-3 w-full skeleton rounded" />
        </div>
      ) : isError || !use ? (
        <div className="mt-3 flex items-center gap-3">
          <p className="text-xs text-amber-300" data-testid="jarvis-use-error">Your Jarvis use could not be loaded.</p>
          <button type="button" onClick={() => void refetch()} className="glass-button min-h-11 px-3 text-xs">
            Try again
          </button>
        </div>
      ) : !use.has_data || top.length === 0 ? (
        <p className="mt-2 text-xs text-slate-400" data-testid="jarvis-use-empty">
          Nothing recorded in the last {use.days} days. Open a page, play something or read a chapter and it shows up here.
        </p>
      ) : (
        <ul className="mt-3 space-y-2" data-testid="jarvis-use-list">
          {top.map((row) => (
            <li key={row.label} className="space-y-1 text-xs">
              <div className="flex justify-between gap-2">
                <span className="truncate text-slate-200">{row.label}</span>
                <span className="shrink-0 tabular-nums text-slate-400">{row.count}</span>
              </div>
              <div className="h-1.5 rounded-full bg-white/5">
                <div className="h-1.5 rounded-full bg-purple-400/70" style={{ width: `${(row.count / busiest) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}

      <p className="mt-2 text-[10px] text-slate-600">Only your own use, on your own screen. The record itself stays private.</p>
    </section>
  );
}
