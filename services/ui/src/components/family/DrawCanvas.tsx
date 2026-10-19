import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Download, Eraser, Loader2, Undo2, Users, Wand2 } from 'lucide-react';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import { storageGetSync } from '../../lib/storage';
import {
  drawingBaseUrl,
  drawingIdentity,
  drawingSocketUrl,
  isDrawingSegment,
  type DrawingSegment,
} from '../../lib/drawingSocket';
import SendToChat from '../chat/SendToChat';
import { canvasPoint, type Point } from './canvasPoint';

const COLORS = ['#1e293b', '#dc2626', '#2563eb', '#16a34a', '#9333ea', '#ea580c'];
const WIDTHS = [3, 6, 12];
const BOARD_RE = /^[A-Za-z0-9_-]{1,64}$/;
const CANVAS_WIDTH = 640;
const CANVAS_HEIGHT = 416;

let segmentCounter = 0;

function nextSegmentId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  segmentCounter += 1;
  return `s${Date.now().toString(36)}${segmentCounter.toString(36)}`;
}

function paintSegment(context: CanvasRenderingContext2D, segment: DrawingSegment): void {
  context.strokeStyle = segment.color;
  context.lineWidth = segment.width;
  context.lineCap = 'round';
  context.lineJoin = 'round';
  context.beginPath();
  context.moveTo(segment.x1, segment.y1);
  context.lineTo(segment.x2, segment.y2);
  context.stroke();
}

/**
 * Family drawing canvas with a live shared board and a "make it way better"
 * pass.
 *
 * Deliberately dependency-free: pointer events, a PNG export, a tiny gateway
 * WebSocket relay and the existing image-edit proxy. No new build-time
 * dependency means no bundle risk on a device that already has to download an
 * APK, and the same component can be swapped for Excalidraw later without
 * changing who calls it.
 */
export default function DrawCanvas() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const drawing = useRef(false);
  const last = useRef<Point | null>(null);
  const touched = useRef(false);
  const segments = useRef<DrawingSegment[]>([]);
  const socket = useRef<WebSocket | null>(null);
  const identity = useMemo(() => drawingIdentity(), []);

  const [color, setColor] = useState(COLORS[0]);
  const [width, setWidth] = useState(WIDTHS[1]);
  const [improving, setImproving] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [board, setBoard] = useState('family');
  const [live, setLive] = useState(false);
  const [status, setStatus] = useState<'off' | 'connecting' | 'live' | 'offline'>('off');
  const [drawers, setDrawers] = useState(0);
  const [liveError, setLiveError] = useState<string | null>(null);
  const improvingRequest = useRef<AbortController | null>(null);

  const ctx = useCallback(() => canvasRef.current?.getContext('2d') ?? null, []);

  const redraw = useCallback(() => {
    const context = ctx();
    const canvas = canvasRef.current;
    if (!context || !canvas) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    segments.current.forEach((segment) => paintSegment(context, segment));
    touched.current = segments.current.length > 0;
  }, [ctx]);

  const pos = (event: React.PointerEvent<HTMLCanvasElement>): Point =>
    canvasPoint(event.clientX, event.clientY, event.currentTarget.getBoundingClientRect(), event.currentTarget);

  const start = (event: React.PointerEvent<HTMLCanvasElement>) => {
    // Record the intent to draw before touching the context: whether a 2D
    // context is available is the renderer's business, not the user's.
    touched.current = true;
    const context = ctx();
    if (!context) return;
    drawing.current = true;
    last.current = pos(event);
    // Pointer capture is not implemented everywhere (and throws in some test
    // environments); drawing still works without it.
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      /* drawing continues without capture */
    }
  };

  const move = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const context = ctx();
    if (!context || !drawing.current || !last.current) return;
    event.preventDefault();
    const point = pos(event);
    const segment: DrawingSegment = {
      id: nextSegmentId(),
      x1: last.current.x,
      y1: last.current.y,
      x2: point.x,
      y2: point.y,
      color,
      width,
      user: identity.username,
      name: identity.name,
    };
    segments.current.push(segment);
    paintSegment(context, segment);
    if (socket.current?.readyState === WebSocket.OPEN) {
      socket.current.send(JSON.stringify({ type: 'segment', segment }));
    }
    last.current = point;
  };

  const end = () => {
    drawing.current = false;
    last.current = null;
  };

  const clear = () => {
    const context = ctx();
    const canvas = canvasRef.current;
    if (!context || !canvas) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    segments.current = [];
    touched.current = false;
    setError(null);
    if (socket.current?.readyState === WebSocket.OPEN) {
      socket.current.send(JSON.stringify({ type: 'clear' }));
    }
  };

  const undo = () => {
    const own = [...segments.current].reverse().find((segment) => !segment.user || segment.user === identity.username);
    if (!own) return;
    segments.current = segments.current.filter((segment) => segment.id !== own.id);
    redraw();
    if (socket.current?.readyState === WebSocket.OPEN) {
      socket.current.send(JSON.stringify({ type: 'undo', id: own.id }));
    }
  };

  // Leaving the Create tab must stop the job poll and close the live board,
  // not keep either running against an unmounted component.
  useEffect(
    () => () => {
      improvingRequest.current?.abort();
      socket.current?.close();
      socket.current = null;
    },
    [],
  );

  const toPng = (): string => {
    try {
      return canvasRef.current?.toDataURL('image/png') ?? '';
    } catch {
      // A canvas implementation without PNG export still draws fine; it just
      // cannot be improved or shared.
      return '';
    }
  };

  // "Blank" means nobody has drawn on it yet. Reading pixels back on every
  // click is both expensive and unreliable on some canvas implementations, so
  // the stroke count is the source of truth.
  const isBlank = (): boolean => !touched.current;

  const connect = () => {
    const room = board.trim();
    if (!BOARD_RE.test(room)) {
      setLiveError('Board codes use letters, numbers, dashes and underscores.');
      return;
    }
    const token = storageGetSync('jarvis_api_key');
    if (!token) {
      setLiveError('Sign in to draw together.');
      return;
    }
    if (touched.current && !window.confirm('Joining the shared board replaces this canvas with the board drawing. Continue?')) {
      return;
    }

    socket.current?.close();
    setLive(true);
    setStatus('connecting');
    setLiveError(null);

    let ws: WebSocket;
    try {
      ws = new WebSocket(drawingSocketUrl(drawingBaseUrl(), token, room));
    } catch {
      setLive(false);
      setStatus('offline');
      setLiveError('The shared board could not connect.');
      return;
    }
    socket.current = ws;

    ws.onmessage = (event) => {
      let message: Record<string, unknown>;
      try {
        message = JSON.parse(String(event.data)) as Record<string, unknown>;
      } catch {
        return;
      }
      const type = message.type;
      if (type === 'welcome') {
        const incoming = Array.isArray(message.segments)
          ? message.segments.filter(isDrawingSegment).slice(0, 5000)
          : [];
        segments.current = incoming;
        redraw();
        setDrawers(Array.isArray(message.participants) ? message.participants.length : 1);
        setStatus('live');
      } else if (type === 'segment' && isDrawingSegment(message.segment)) {
        const segment = message.segment;
        if (!segments.current.some((existing) => existing.id === segment.id)) {
          segments.current.push(segment);
          const context = ctx();
          if (context) paintSegment(context, segment);
          touched.current = true;
        }
      } else if (type === 'undo' && typeof message.id === 'string') {
        segments.current = segments.current.filter((segment) => segment.id !== message.id);
        redraw();
      } else if (type === 'clear') {
        segments.current = [];
        const context = ctx();
        const canvas = canvasRef.current;
        if (context && canvas) context.clearRect(0, 0, canvas.width, canvas.height);
        touched.current = false;
      } else if (type === 'participant-joined') {
        setDrawers((count) => count + 1);
      } else if (type === 'participant-left') {
        setDrawers((count) => Math.max(0, count - 1));
      } else if (type === 'error') {
        setLiveError(typeof message.message === 'string' ? message.message : 'The shared board refused that.');
      }
    };

    ws.onerror = () => {
      setLiveError('The shared board could not connect.');
    };

    ws.onclose = () => {
      if (socket.current !== ws) return;
      socket.current = null;
      setLive(false);
      setStatus('offline');
      setDrawers(0);
    };
  };

  const disconnect = () => {
    const ws = socket.current;
    socket.current = null;
    ws?.close();
    setLive(false);
    setStatus('off');
    setDrawers(0);
    setLiveError(null);
  };

  const improve = async () => {
    if (isBlank()) {
      setError('Draw something first');
      return;
    }
    improvingRequest.current?.abort();
    const controller = new AbortController();
    improvingRequest.current = controller;
    setImproving(true);
    setError(null);
    try {
      const edited = await api.editImage(
        { prompt: 'Make this drawing much better', image: toPng() },
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      const raw = edited?.data?.[0]?.b64_json ?? edited?.data?.[0]?.url;
      if (edited?.status === 'SUCCESS' && raw) {
        setResult(raw.startsWith('data:') ? raw : `data:image/png;base64,${raw}`);
      } else {
        setError(edited?.message || 'The image backend did not return a picture');
      }
    } catch (err) {
      // Leaving the tab aborts the poll; that is not a failure to show.
      if (controller.signal.aborted) return;
      // The gateway's reason (no model configured, backend down) rather than
      // axios's bare "Request failed with status code 502".
      setError(errorText(err, 'Could not improve that drawing'));
    } finally {
      if (improvingRequest.current === controller) {
        improvingRequest.current = null;
        // An abort means the tab is gone; there is no mounted component left
        // to show a spinner in.
        if (!controller.signal.aborted) setImproving(false);
      }
    }
  };

  const download = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const link = document.createElement('a');
    link.download = 'jarvis-drawing.png';
    link.href = canvas.toDataURL('image/png');
    link.click();
  };

  return (
    <div className="glass-panel p-4 rounded-2xl border border-white/5 space-y-3" data-testid="draw-canvas">
      {/* Two rows that fit a phone: colours, then brush sizes and tools. */}
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Colour">
        {COLORS.map((c) => (
          <button
            key={c}
            type="button"
            aria-label={`Colour ${c}`}
            aria-pressed={color === c}
            onClick={() => setColor(c)}
            className="flex h-11 w-11 items-center justify-center"
          >
            <span
              className={`h-9 w-9 rounded-full border-2 ${color === c ? 'border-white' : 'border-white/20'}`}
              style={{ background: c }}
            />
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {WIDTHS.map((w) => (
          <button
            key={w}
            type="button"
            aria-label={`Brush ${w}`}
            aria-pressed={width === w}
            onClick={() => setWidth(w)}
            className={`glass-button flex h-11 w-11 items-center justify-center p-0 ${width === w ? 'border-purple-400/50' : ''}`}
          >
            <span className="rounded-full bg-slate-200" style={{ width: w + 2, height: w + 2 }} />
          </button>
        ))}
        <span className="mx-1 h-6 w-px bg-white/10" aria-hidden />
        <button type="button" onClick={undo} className="glass-button flex h-11 w-11 items-center justify-center p-0" aria-label="Undo" title="Undo">
          <Undo2 size={16} />
        </button>
        <button type="button" onClick={clear} className="glass-button flex h-11 w-11 items-center justify-center p-0" aria-label="Clear" title="Clear">
          <Eraser size={16} />
        </button>
        <button type="button" onClick={download} className="glass-button flex h-11 w-11 items-center justify-center p-0" aria-label="Download" title="Download">
          <Download size={16} />
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2" data-testid="drawing-live-controls">
        <button
          type="button"
          onClick={live ? disconnect : connect}
          aria-pressed={live}
          className={`glass-button min-h-11 px-3 py-2 text-sm ${live ? 'border-purple-400/50' : ''}`}
        >
          {status === 'connecting' ? <Loader2 size={14} className="animate-spin" /> : <Users size={14} />}
          {live ? 'Leave shared board' : 'Draw together'}
        </button>
        {!live && (
          <label className="flex min-h-11 items-center gap-2 text-xs text-slate-400">
            Board
            <input
              aria-label="Drawing board code"
              value={board}
              maxLength={64}
              onChange={(event) => setBoard(event.target.value.replace(/[^A-Za-z0-9_-]/g, '').slice(0, 64))}
              className="glass-input w-28 px-2 py-1.5 text-sm"
            />
          </label>
        )}
        {status === 'live' && (
          <span className="text-xs text-emerald-300" data-testid="drawing-live-status">
            Live · {drawers} {drawers === 1 ? 'person' : 'people'}
          </span>
        )}
        {status === 'connecting' && <span className="text-xs text-slate-400">Connecting…</span>}
        {status === 'offline' && <span className="text-xs text-amber-300">Shared board disconnected</span>}
        {liveError && (
          <span className="text-xs text-rose-300" role="alert">
            {liveError}
          </span>
        )}
      </div>

      <canvas
        ref={canvasRef}
        width={CANVAS_WIDTH}
        // Both sides are multiples of 32: image edit models work on 16 or 32
        // pixel blocks and some refuse other sizes.
        height={CANVAS_HEIGHT}
        data-testid="draw-surface"
        onPointerDown={start}
        onPointerMove={move}
        onPointerUp={end}
        onPointerCancel={end}
        className="w-full h-auto rounded-xl bg-white touch-none select-none"
        style={{ touchAction: 'none' }}
      />

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={improve}
          disabled={improving}
          className="glass-button min-h-11 px-3 py-2 text-sm disabled:opacity-60"
        >
          {improving ? <Loader2 size={14} className="animate-spin" /> : <Wand2 size={14} />} Make it way better
        </button>
        {improving && <span className="text-xs text-slate-400">This can take a few minutes</span>}
        <SendToChat getDataUrl={() => (isBlank() ? '' : toPng())} fileName="drawing.png" emptyMessage="Draw something first" />
        {error && (
          <span className="text-xs text-rose-300" role="alert">
            {error}
          </span>
        )}
      </div>

      {result && (
        <div className="space-y-1" data-testid="draw-result">
          <p className="text-[11px] uppercase tracking-wider text-slate-500">Jarvis improved it</p>
          <img src={result} alt="Improved drawing" className="w-full max-w-md rounded-xl border border-white/10" />
          <SendToChat getDataUrl={() => result} fileName="drawing-improved.png" label="Send this one" />
        </div>
      )}
    </div>
  );
}
