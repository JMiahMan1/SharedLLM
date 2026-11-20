import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { ArrowDown, Download, FileCode, Pause, Play, ScrollText, X } from 'lucide-react';
import { failureText } from '../../lib/discoveredDevices';
import {
  VIEW_LEVELS, VIEW_LEVEL_TEXT, levelLetter, logText, matches, mergeItems, requestConfigDump, shownAt,
  streamDeviceLogs, timeText, type LogItem, type LogMarkerItem, type StreamItem, type ViewLevel,
} from '../../lib/deviceLogs';
import { NdjsonError } from '../../services/api';

const CARD = 'rounded-2xl border border-white/10 bg-white/[0.03]';
const INPUT = 'min-h-11 w-full min-w-0 rounded-xl border border-white/10 bg-black/30 px-3 text-sm text-white';
const BTN = 'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.04] px-3 text-sm text-slate-200 hover:bg-white/10 disabled:opacity-50';

/** Lines kept on screen (the hub keeps as many). */
export const MAX_ITEMS = 2000;
/** New lines are drawn in batches this often, so a chatty device does not redraw the page per line. */
const FLUSH_MS = 120;
const RETRY_START_MS = 2000;
const RETRY_MAX_MS = 30_000;

const LEVEL_TONE: Record<string, string> = {
  error: 'text-rose-300',
  warning: 'text-amber-200',
  info: 'text-emerald-300',
  config: 'text-sky-300',
  debug: 'text-slate-300',
  verbose: 'text-slate-400',
  very_verbose: 'text-slate-400',
};

const MARKER_TONE: Record<string, string> = {
  offline: 'border-rose-400/30 bg-rose-500/15 text-rose-100',
  restart: 'border-rose-400/30 bg-rose-500/15 text-rose-100',
  online: 'border-emerald-400/30 bg-emerald-500/15 text-emerald-100',
  drop: 'border-sky-400/30 bg-sky-500/15 text-sky-100',
  unknown: 'border-amber-400/30 bg-amber-500/15 text-amber-100',
};
const MARKER_DEFAULT = 'border-white/10 bg-white/[0.06] text-slate-200';

type Status = 'idle' | 'connecting' | 'live' | 'offline' | 'retrying' | 'refused';

function Marker({ item }: { item: LogMarkerItem }) {
  return (
    <div className="my-1 flex justify-center" data-testid="log-marker" data-event={item.event}>
      <span className={`max-w-full rounded-full border px-2.5 py-0.5 text-center font-sans text-[11px] font-medium ${MARKER_TONE[item.event] ?? MARKER_DEFAULT}`}>
        {timeText(item.at)} · {item.message}
      </span>
    </div>
  );
}

/**
 * Devices > an ESPHome device > Logs: the device's live log, like Home Assistant's ESPHome dashboard "Logs"
 * button. Opens on request (while it is open the hub asks the device for the chosen level); live tail with
 * auto-scroll and pause, level filter, search, a config dump, a download, and the restart monitor's
 * offline/reconnected/restarted markers in line.
 */
export default function DeviceLogsSection({ deviceId, deviceName }: { deviceId: string; deviceName: string }) {
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState<ViewLevel>('debug');
  const [search, setSearch] = useState('');
  const [paused, setPaused] = useState(false);
  const [items, setItems] = useState<LogItem[]>([]);
  const [held, setHeld] = useState<LogItem[] | null>(null); // what is on screen while paused
  const [status, setStatus] = useState<Status>('idle');
  const [problem, setProblem] = useState<string | null>(null);
  const [atEnd, setAtEnd] = useState(true);
  const pane = useRef<HTMLDivElement | null>(null);
  const pending = useRef<LogItem[]>([]);
  const lastSeq = useRef<number | null>(null);

  const flush = useCallback(() => {
    const batch = pending.current;
    if (!batch.length) return;
    pending.current = [];
    setItems((cur) => mergeItems(cur, batch, MAX_ITEMS));
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const ctrl = new AbortController();
    let retry = RETRY_START_MS;
    let timer: ReturnType<typeof setInterval> | null = setInterval(flush, FLUSH_MS);
    lastSeq.current = null;
    pending.current = [];

    const onItem = (item: StreamItem) => {
      if (item.type === 'hello') {
        setStatus(item.connected ? 'live' : 'offline');
        retry = RETRY_START_MS;
        return;
      }
      if (item.type === 'ping' || item.type === 'end') return;
      if (item.seq !== null) lastSeq.current = Math.max(lastSeq.current ?? 0, item.seq);
      if (item.type === 'marker' && item.event === 'offline') setStatus('offline');
      if (item.type === 'marker' && item.event === 'online') setStatus('live');
      pending.current.push(item);
    };

    (async () => {
      while (!ctrl.signal.aborted) {
        try {
          await streamDeviceLogs(deviceId, level, lastSeq.current, onItem, ctrl.signal);
          // The hub ends a stream after half an hour: reopen it from the last line, keeping what is on screen.
          if (ctrl.signal.aborted) return;
          await new Promise((r) => setTimeout(r, 300));
        } catch (err) {
          if (ctrl.signal.aborted) return;
          const status = err instanceof NdjsonError ? err.status : 0;
          if (status === 401 || status === 403 || status === 404 || status === 409) {
            setStatus('refused');
            setProblem(err instanceof Error ? err.message : String(err));
            return;
          }
          setStatus('retrying');
          setProblem(err instanceof Error ? err.message : String(err));
          await new Promise((r) => setTimeout(r, retry));
          retry = Math.min(retry * 2, RETRY_MAX_MS);
        }
      }
    })();

    return () => {
      ctrl.abort();
      if (timer) clearInterval(timer);
      timer = null;
    };
  }, [open, level, deviceId, flush]);

  const shown = useMemo(() => (held ?? items).filter((i) => shownAt(i, level) && matches(i, search)),
    [held, items, level, search]);
  const waiting = held ? Math.max(0, items.length - held.length) : 0;

  useLayoutEffect(() => {
    const el = pane.current;
    if (el && atEnd && !held) el.scrollTop = el.scrollHeight;
  }, [shown, atEnd, held]);

  const onScroll = () => {
    const el = pane.current;
    if (el) setAtEnd(el.scrollHeight - el.scrollTop - el.clientHeight < 24);
  };

  /** A new view (opened, or another level): the stream starts again from the hub's kept lines. */
  const restart = (next: ViewLevel) => {
    setLevel(next);
    setItems([]);
    setHeld(null);
    setPaused(false);
    setProblem(null);
    setStatus('connecting');
    setAtEnd(true);
  };

  const togglePause = () => {
    if (paused) {
      setHeld(null);
      setPaused(false);
      setAtEnd(true);
    } else {
      setHeld(items);
      setPaused(true);
    }
  };

  const dump = useMutation({
    mutationFn: () => requestConfigDump(deviceId),
    onSuccess: () => {
      if (level === 'error' || level === 'warning') restart('info'); // the dump is logged at the config level
      else if (paused) togglePause();
      toast.success('Asked the device for its configuration.');
    },
    onError: (err) => toast.error(failureText(err)),
  });

  const download = () => {
    const blob = new Blob([logText(shown)], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const safe = deviceName.replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'device';
    a.href = url;
    a.download = `${safe}-log.txt`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const close = () => {
    setOpen(false);
    setPaused(false);
    setHeld(null);
    setStatus('idle');
  };

  const statusText = {
    idle: '',
    connecting: 'Connecting…',
    live: paused ? `Paused${waiting ? `, ${waiting} new line${waiting === 1 ? '' : 's'} waiting` : ''}` : 'Live',
    offline: 'Device offline. New lines appear when it reconnects.',
    retrying: `The log stream was interrupted${problem ? ` (${problem})` : ''}. Trying again…`,
    refused: problem ?? 'The log cannot be shown.',
  }[status];

  return (
    <section aria-label="Logs" className="space-y-2" data-testid="section-logs">
      <h3 className="flex items-center gap-2 px-1 text-sm font-semibold text-slate-300">
        <ScrollText size={14} aria-hidden /> Logs
      </h3>
      <div className={`${CARD} space-y-3 p-3`}>
        {!open ? (
          <div className="space-y-2">
            <p className="text-sm text-slate-300">
              The device&apos;s own log, live. While it is open the device sends lines at the level you choose.
            </p>
            <button type="button" className={BTN} onClick={() => { restart(level); setOpen(true); }}>
              <ScrollText size={14} aria-hidden /> Show live logs
            </button>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-[minmax(0,9rem)_minmax(0,1fr)]">
              <label className="block text-xs font-medium text-slate-400">
                Level
                <select className={`mt-1 ${INPUT}`} value={level} aria-label="Log level"
                  onChange={(e) => restart(e.target.value as ViewLevel)}>
                  {VIEW_LEVELS.map((l) => <option key={l} value={l}>{VIEW_LEVEL_TEXT[l]}</option>)}
                </select>
              </label>
              <label className="block text-xs font-medium text-slate-400">
                Search
                <input type="search" className={`mt-1 ${INPUT}`} value={search} placeholder="Text or tag"
                  aria-label="Search the log" onChange={(e) => setSearch(e.target.value)} />
              </label>
            </div>
            <div className="flex flex-wrap gap-2">
              <button type="button" className={BTN} onClick={togglePause} aria-pressed={paused}>
                {paused ? <Play size={14} aria-hidden /> : <Pause size={14} aria-hidden />}
                {paused ? 'Resume' : 'Pause'}
              </button>
              <button type="button" className={BTN} disabled={dump.isPending || status === 'offline'}
                onClick={() => dump.mutate()}>
                <FileCode size={14} aria-hidden /> Show config dump
              </button>
              <button type="button" className={BTN} disabled={!shown.length} onClick={download}>
                <Download size={14} aria-hidden /> Download
              </button>
              <button type="button" className={BTN} onClick={close}>
                <X size={14} aria-hidden /> Close
              </button>
            </div>
            <p className={`text-xs ${status === 'offline' || status === 'refused' ? 'text-amber-300/90' : 'text-slate-400'}`}
              data-testid="log-status" role="status">
              {statusText}
            </p>
            <div className="relative">
              <div ref={pane} onScroll={onScroll} role="log" aria-label="Device log" aria-live="off" tabIndex={0}
                data-testid="device-log"
                className="h-[55vh] max-h-[36rem] min-h-48 overflow-y-auto overflow-x-hidden rounded-xl bg-black/40 p-2 font-mono text-[11px] leading-snug text-slate-300">
                {shown.length === 0 && (
                  <p className="p-2 font-sans text-xs text-slate-500">
                    {search ? 'No lines match.' : status === 'connecting' ? 'Waiting for the device…' : 'No lines yet.'}
                  </p>
                )}
                {shown.map((item, i) => (item.type === 'marker'
                  ? <Marker key={`m${item.seq ?? `x${i}`}`} item={item} />
                  : (
                    <div key={item.seq} className="whitespace-pre-wrap break-all" data-testid="log-line" data-level={item.level}>
                      <span className="text-slate-500">{timeText(item.at)} </span>
                      <span className={`font-semibold ${LEVEL_TONE[item.level] ?? ''}`}>[{levelLetter(item.level)}]</span>
                      {item.tag && <span className="text-slate-400">[{item.tag}]</span>}{' '}
                      <span className={item.level === 'error' ? 'text-rose-300' : item.level === 'warning' ? 'text-amber-200' : undefined}>
                        {item.message}
                      </span>
                    </div>
                  )))}
              </div>
              {!atEnd && !paused && shown.length > 0 && (
                <button type="button" className={`${BTN} absolute bottom-2 right-2 bg-slate-900/90`}
                  onClick={() => { setAtEnd(true); }}>
                  <ArrowDown size={14} aria-hidden /> Latest
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
