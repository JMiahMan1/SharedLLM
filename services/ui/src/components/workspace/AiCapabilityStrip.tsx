import { useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, CircleHelp, Loader2, XCircle } from 'lucide-react';
import type { AiCapability } from '../../types/api';
import { statusOf, summarise, toneFor } from '../../lib/aiCaps';

type Props = {
  capabilities: AiCapability[];
  loading?: boolean;
  /** Called when the user asks to re-check; omitted renders no refresh control. */
  onRefresh?: () => void;
};

/**
 * Shows which AI tools can actually run, and why not when they can't.
 *
 * The point of this panel is the difference between *installed* and *working*:
 * the image backend advertises three models and can still reject every call, so
 * a model dropdown on its own tells a user nothing about whether editing will
 * succeed. Unconfirmed is rendered as its own state rather than being rounded to
 * yes, because that is what the backend actually knows.
 */
export default function AiCapabilityStrip({ capabilities, loading, onRefresh }: Props) {
  const [open, setOpen] = useState(false);

  const summary = useMemo(() => summarise(capabilities), [capabilities]);

  if (loading && capabilities.length === 0) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 text-[11px] text-slate-400">
        <Loader2 size={12} className="animate-spin" /> Checking AI tools…
      </div>
    );
  }
  if (capabilities.length === 0) return null;

  const tone =
    toneFor(summary) === 'warn'
      ? 'text-amber-300 border-amber-400/30'
      : toneFor(summary) === 'neutral'
        ? 'text-slate-300 border-white/10'
        : 'text-emerald-300 border-emerald-400/30';

  return (
    <div className={`rounded border ${tone} bg-black/30 mb-3`}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label="AI tool availability"
        className="w-full flex items-center gap-2 px-3 py-2 text-[11px] text-left min-h-11 pointer-coarse:min-h-11"
      >
        {summary.unavailable > 0 ? (
          <AlertTriangle size={12} className="shrink-0" />
        ) : summary.unconfirmed > 0 ? (
          <CircleHelp size={12} className="shrink-0" />
        ) : (
          <CheckCircle2 size={12} className="shrink-0" />
        )}
        <span className="flex-1">
          AI tools: {summary.ready}/{summary.total} ready
          {summary.unconfirmed > 0 ? `, ${summary.unconfirmed} unconfirmed` : ''}
        </span>
        <span className="text-slate-500">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <ul className="border-t border-white/10 px-3 py-2 space-y-1.5">
          {capabilities.map((cap) => {
            const s = statusOf(cap);
            return (
              <li key={cap.key} className="text-[11px] leading-snug">
                <span className="flex items-center gap-1.5">
                  {s === 'ok' ? (
                    <CheckCircle2 size={11} className="text-emerald-400 shrink-0" />
                  ) : s === 'no' ? (
                    <XCircle size={11} className="text-red-400 shrink-0" />
                  ) : (
                    <CircleHelp size={11} className="text-slate-400 shrink-0" />
                  )}
                  <span className={s === 'ok' ? 'text-emerald-200' : s === 'no' ? 'text-red-200' : 'text-slate-300'}>
                    {cap.label}
                  </span>
                  <span className="text-slate-500">
                    — {s === 'ok' ? 'ready' : s === 'no' ? 'unavailable' : 'unconfirmed'}
                  </span>
                </span>
                {cap.detail && <p className="ml-5 mt-0.5 text-slate-500">{cap.detail}</p>}
              </li>
            );
          })}
          {onRefresh && (
            <li>
              <button
                onClick={onRefresh}
                className="ml-5 mt-1 text-[11px] text-indigo-300 hover:text-indigo-200 min-h-11 pointer-coarse:min-h-11"
              >
                Re-check
              </button>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
