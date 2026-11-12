import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, Loader2 } from 'lucide-react';
import Modal from '../ui/Modal';
import { errorText } from '../../lib/errorText';
import { getTraces, relativeAgo, TRACE_RESULT_LABELS, type AutomationItem, type Trace } from '../../lib/automations';
import { BUTTON } from './styles';

const RESULT_TONES: Record<string, string> = {
  finished: 'text-emerald-300',
  running: 'text-sky-300',
  error: 'text-red-300',
  condition_failed: 'text-slate-400',
  stopped: 'text-amber-300',
  cancelled: 'text-slate-400',
};

function clock(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit', second: '2-digit' });
}

/** The item's recent runs; tap one for what set it off, whether its conditions passed, and each step. */
export default function TraceViewer({ item, onClose }: { item: AutomationItem; onClose: () => void }) {
  const traces = useQuery({
    queryKey: ['homehub-traces', item.kind, item.id],
    queryFn: () => getTraces(item.kind, item.id),
    refetchInterval: 10_000,
    retry: false,
  });
  const [open, setOpen] = useState<Trace | null>(null);

  return (
    <Modal isOpen onClose={onClose} title={`Runs of ${item.alias || item.id}`} size="2xl">
      {traces.isLoading && <div className="flex justify-center py-8" role="status" aria-label="Loading"><Loader2 className="animate-spin text-slate-500" /></div>}
      {traces.isError && <p className="text-sm text-red-300">{errorText(traces.error, 'Could not load the runs')}</p>}
      {traces.data && !open && (
        traces.data.length === 0 ? (
          <p className="text-sm text-slate-400">It has not run since the hub started keeping runs.</p>
        ) : (
          <ul className="divide-y divide-white/5" data-testid="trace-list">
            {traces.data.map((t) => (
              <li key={t.run_id}>
                <button type="button" className="flex min-h-11 w-full items-center justify-between gap-3 py-2 text-left" onClick={() => setOpen(t)}>
                  <span className="min-w-0">
                    <span className="block text-sm text-white">{clock(t.started)}</span>
                    <span className="block truncate text-xs text-slate-500">{t.trigger?.description || 'Run'}</span>
                  </span>
                  <span className={`shrink-0 text-xs ${RESULT_TONES[t.result] ?? 'text-slate-300'}`}>{TRACE_RESULT_LABELS[t.result] ?? t.result}</span>
                </button>
              </li>
            ))}
          </ul>
        )
      )}
      {open && <TraceDetail trace={open} onBack={() => setOpen(null)} />}
    </Modal>
  );
}

export function TraceDetail({ trace, onBack }: { trace: Trace; onBack: () => void }) {
  const trigger = trace.trigger ?? {};
  const extra = Object.entries(trigger).filter(([k]) => k !== 'description');
  return (
    <div className="space-y-4" data-testid="trace-detail">
      <button type="button" className={BUTTON} onClick={onBack}><ChevronLeft size={14} /> All runs</button>
      <div className="text-sm">
        <p className="text-white">{clock(trace.started)} <span className="text-slate-500">({relativeAgo(trace.started)})</span></p>
        <p className={RESULT_TONES[trace.result] ?? 'text-slate-300'}>{TRACE_RESULT_LABELS[trace.result] ?? trace.result}</p>
        {trace.error && <p className="mt-1 text-red-300" role="alert">{trace.error}</p>}
      </div>
      <section>
        <h4 className="text-[10px] font-black uppercase tracking-widest text-slate-400">Trigger</h4>
        <p className="text-sm text-slate-200">{trigger.description || 'Started by hand'}</p>
        {extra.length > 0 && (
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
            {extra.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-slate-500">{k}</dt>
                <dd className="break-all text-slate-300">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>
              </div>
            ))}
          </dl>
        )}
      </section>
      <section>
        <h4 className="text-[10px] font-black uppercase tracking-widest text-slate-400">Conditions</h4>
        <p className="text-sm text-slate-200">
          {trace.conditions?.passed === null || trace.conditions?.passed === undefined ? 'None checked'
            : trace.conditions.passed ? 'Passed' : 'Not met'}
          {trace.conditions?.detail ? `: ${trace.conditions.detail}` : ''}
        </p>
      </section>
      <section>
        <h4 className="text-[10px] font-black uppercase tracking-widest text-slate-400">Steps</h4>
        {trace.steps.length === 0 ? <p className="text-sm text-slate-500">No steps ran.</p> : (
          <ol className="space-y-1">
            {trace.steps.map((s) => (
              <li key={`${s.index}-${s.at}`} className="rounded-lg border border-white/5 px-3 py-2" style={{ marginLeft: `${Math.min(s.depth, 4) * 12}px` }}>
                <p className="text-sm text-white">{s.alias || s.summary}</p>
                <p className="text-[11px] text-slate-500">{s.kind} · {clock(s.at)}</p>
                {s.error && <p className="text-xs text-red-300">{s.error}</p>}
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
