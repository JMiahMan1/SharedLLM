import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { ChevronRight, Copy, RotateCcw } from 'lucide-react';
import { failureText } from '../../lib/discoveredDevices';
import { ago } from '../../lib/hardware';
import {
  KIND_TEXT, LEVEL_TEXT, countsText, durationText, getDeviceRestarts, memoryLines, restartsKey, setRestartLogLevel,
  type DeviceRestarts, type LogLevel, type LogLine, type RestartEvent,
} from '../../lib/restarts';

const CARD = 'rounded-2xl border border-white/10 bg-white/[0.03]';
const INPUT = 'min-h-11 w-full min-w-0 rounded-xl border border-white/10 bg-black/30 px-3 text-sm text-white';
const BTN = 'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.04] px-3 text-sm text-slate-200 hover:bg-white/10 disabled:opacity-50';

const KIND_TONE: Record<RestartEvent['kind'], string> = {
  restart: 'border-rose-400/30 bg-rose-500/15 text-rose-100',
  drop: 'border-sky-400/30 bg-sky-500/15 text-sky-100',
  unknown: 'border-amber-400/30 bg-amber-500/15 text-amber-100',
};

function Lines({ lines, label }: { lines: LogLine[]; label: string }) {
  if (!lines.length) return null;
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-slate-400">{label}</p>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-black/40 p-2 font-mono text-[11px] leading-snug text-slate-300"
        data-testid="restart-lines" tabIndex={0} aria-label={label}>
        {lines.map(([, level, text], i) => (
          <span key={i} className={level === 'E' ? 'text-rose-300' : level === 'W' ? 'text-amber-200' : undefined}>
            {text}{'\n'}
          </span>
        ))}
      </pre>
    </div>
  );
}

function EventRow({ ev }: { ev: RestartEvent }) {
  const memory = memoryLines(ev.memory);
  return (
    <li data-testid="restart-event">
      <details className="group">
        <summary className="flex min-h-12 cursor-pointer list-none items-start gap-3 py-2 [&::-webkit-details-marker]:hidden">
          <span className={`mt-0.5 shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${KIND_TONE[ev.kind]}`}>
            {KIND_TEXT[ev.kind]}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm text-slate-100">{ev.summary}</span>
            <span className="block text-xs text-slate-400">
              {ago(ev.at)}{ev.cause ? ` · ${ev.cause}` : ''}
            </span>
          </span>
          <ChevronRight size={16} className="mt-1 shrink-0 text-slate-500 motion-safe:transition-transform group-open:rotate-90" aria-hidden />
        </summary>
        <div className="space-y-3 pb-3 text-sm" data-testid="restart-event-detail">
          <dl className="space-y-1 text-xs text-slate-300">
            <div><dt className="inline text-slate-500">How it was told: </dt><dd className="inline">{ev.evidence}</dd></div>
            {ev.reset_reason && <div><dt className="inline text-slate-500">Reset reason: </dt><dd className="inline">{ev.reset_reason}</dd></div>}
            {!ev.reset_reason && ev.last_reset_reason && (
              <div><dt className="inline text-slate-500">Reset reason of its last boot: </dt><dd className="inline">{ev.last_reset_reason}</dd></div>
            )}
            {ev.last_crash && <div><dt className="inline text-slate-500">Last crash: </dt><dd className="inline">{ev.last_crash}</dd></div>}
            {ev.hint && <div><dt className="inline text-slate-500">Likely: </dt><dd className="inline">{ev.hint}</dd></div>}
            {ev.error && <div><dt className="inline text-slate-500">The hub saw: </dt><dd className="inline break-all">{ev.error}</dd></div>}
            {ev.silent_s !== null && ev.silent_s >= 5 && (
              <div><dt className="inline text-slate-500">Silent before: </dt><dd className="inline">{durationText(ev.silent_s)}</dd></div>
            )}
            {ev.context_text.map((t) => <div key={t}><dt className="inline text-slate-500">Then: </dt><dd className="inline">{t}</dd></div>)}
            {memory.map((t) => <div key={t}><dt className="sr-only">Memory</dt><dd>{t}</dd></div>)}
          </dl>
          <Lines lines={ev.lines_before} label={`Its last log lines before (${ev.log_level ?? 'warning'} level)`} />
          <Lines lines={ev.lines_after} label="After it came back" />
          {!ev.lines_before.length && !ev.lines_after.length && (
            <p className="text-xs text-slate-500">No log lines were caught around this one.</p>
          )}
          {ev.dropped_lines > 0 && <p className="text-xs text-slate-500">{ev.dropped_lines} lines came too fast and were not kept.</p>}
        </div>
      </details>
    </li>
  );
}

function Guidance({ data }: { data: DeviceRestarts }) {
  const g = data.guidance;
  if (!g) return null;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(g.yaml);
      toast.success('Copied.');
    } catch {
      toast.error('Copying is not allowed here. Select the text instead.');
    }
  };
  return (
    <div className="space-y-2 rounded-2xl border border-amber-400/25 bg-amber-500/10 p-3" data-testid="restart-guidance">
      <p className="text-sm font-semibold text-amber-100">The cause of a restart cannot be read from this device</p>
      <p className="text-sm text-amber-100/80">{g.text}</p>
      <p className="text-xs text-amber-100/70">Missing: {g.missing.join(', ')}.</p>
      <pre className="overflow-auto whitespace-pre rounded-xl bg-black/40 p-2 font-mono text-[11px] leading-snug text-slate-200"
        data-testid="restart-yaml" tabIndex={0} aria-label="ESPHome configuration to add">{g.yaml}</pre>
      <button type="button" className={BTN} onClick={() => void copy()}><Copy size={14} aria-hidden /> Copy</button>
    </div>
  );
}

/**
 * The Restarts section of an ESPHome device: recent restarts and dropped connections, with cause, what the hub
 * was doing and the device's own log lines around each, the log level the hub keeps, and what to add to the
 * device's configuration when it cannot report why it restarted.
 */
export default function RestartsSection({ deviceId }: { deviceId: string }) {
  const qc = useQueryClient();
  const [all, setAll] = useState(false);
  const q = useQuery({ queryKey: restartsKey(deviceId), queryFn: () => getDeviceRestarts(deviceId), retry: false,
    refetchInterval: 30_000 });
  const level = useMutation({
    mutationFn: (l: LogLevel) => setRestartLogLevel(deviceId, l),
    onSuccess: (d) => { qc.setQueryData(restartsKey(deviceId), d); toast.success('Saved.'); },
    onError: (err) => toast.error(failureText(err)),
  });
  const d = q.data;
  if (q.isLoading) return <div className="skeleton h-20 w-full rounded-2xl" role="status" aria-label="Loading restarts" />;
  if (q.error) return (
    <section aria-label="Restarts" className="space-y-2" data-testid="section-restarts">
      <h3 className="px-1 text-sm font-semibold text-slate-300">Restarts</h3>
      <p className="text-sm text-amber-300/90" role="alert">{failureText(q.error)}</p>
    </section>
  );
  if (!d || !d.supported) return null;
  const events = d.events ?? [];
  const shown = all ? events : events.slice(0, 5);
  const counts = countsText(d.counts_24h);
  return (
    <section aria-label="Restarts" className="space-y-2" data-testid="section-restarts">
      <h3 className="flex items-center gap-2 px-1 text-sm font-semibold text-slate-300">
        <RotateCcw size={14} aria-hidden /> Restarts
      </h3>
      <div className={`${CARD} space-y-3 p-3`}>
        <p className="text-sm text-slate-200" data-testid="restart-summary">
          {d.offline ? `Offline for ${durationText(d.offline.for_s)}; what happened is decided once it is back.`
            : counts ? `Last 24 hours: ${counts}.` : 'No restarts or dropped connections recorded.'}
        </p>
        <Guidance data={d} />
        {events.length > 0 && (
          <ul className="divide-y divide-white/5" aria-label="Recent restarts">
            {shown.map((ev) => <EventRow key={ev.id} ev={ev} />)}
          </ul>
        )}
        {events.length > 5 && (
          <button type="button" className={BTN} onClick={() => setAll((v) => !v)}>
            {all ? 'Show fewer' : `Show all ${events.length}`}
          </button>
        )}
        <div className="space-y-1 border-t border-white/5 pt-3">
          <label className="block text-xs font-medium text-slate-400">
            Log lines kept from the device
            <select className={`mt-1 ${INPUT}`} value={d.log_level ?? 'warning'} disabled={level.isPending}
              onChange={(e) => level.mutate(e.target.value as LogLevel)} aria-label="Log lines kept from the device">
              {(d.log_levels ?? ['off', 'error', 'warning', 'info', 'debug']).map((l) => (
                <option key={l} value={l}>{LEVEL_TEXT[l] ?? l}</option>
              ))}
            </select>
          </label>
          <p className="text-[11px] text-slate-500">
            The hub keeps the device&apos;s last {d.buffer?.max ?? 200} lines at this level, on its existing connection, and
            saves them with each restart. Use Debug only while investigating.
            {d.subscribe_error ? ` Not receiving logs: ${d.subscribe_error}.` : ''}
          </p>
        </div>
        {(d.recent_lines?.length ?? 0) > 0 && (
          <details className="group border-t border-white/5 pt-2" data-testid="restart-recent">
            <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between text-xs font-medium text-slate-400 [&::-webkit-details-marker]:hidden">
              Its latest log lines ({d.recent_lines?.length})
              <ChevronRight size={14} className="motion-safe:transition-transform group-open:rotate-90" aria-hidden />
            </summary>
            <Lines lines={d.recent_lines ?? []} label="Latest log lines" />
          </details>
        )}
      </div>
    </section>
  );
}
