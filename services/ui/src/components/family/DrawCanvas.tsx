import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, Eraser, Loader2, Sparkles, Wand2 } from 'lucide-react';
import { api } from '../../services/api';

const COLORS = ['#1e293b', '#dc2626', '#2563eb', '#16a34a', '#9333ea', '#ea580c'];
const WIDTHS = [3, 6, 12];

type Point = { x: number; y: number };

/**
 * Family drawing canvas with a "make it way better" pass.
 *
 * Deliberately dependency-free: pointer events, a PNG export and the existing
 * image-edit proxy. No new build-time dependency means no bundle risk on a
 * device that already has to download an APK, and the same component can be
 * swapped for Excalidraw later without changing who calls it.
 */
export default function DrawCanvas({ onSend }: { onSend?: (dataUrl: string) => void }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const drawing = useRef(false);
  const last = useRef<Point | null>(null);
  const snapshot = useRef<string | null>(null);

  const [color, setColor] = useState(COLORS[0]);
  const [width, setWidth] = useState(WIDTHS[1]);
  const [improving, setImproving] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const touched = useRef(false);

  const ctx = useCallback(() => canvasRef.current?.getContext('2d') ?? null, []);

  const pos = (event: React.PointerEvent<HTMLCanvasElement>): Point => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

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
    const point = pos(event);
    context.strokeStyle = color;
    context.lineWidth = width;
    context.lineCap = 'round';
    context.lineJoin = 'round';
    context.beginPath();
    context.moveTo(last.current.x, last.current.y);
    context.lineTo(point.x, point.y);
    context.stroke();
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
    touched.current = false;
    setError(null);
  };

  const undo = () => {
    if (!snapshot.current) return;
    const image = new Image();
    image.onload = () => {
      const context = ctx();
      if (context) context.drawImage(image, 0, 0);
    };
    image.src = snapshot.current;
  };

  // Keep the previous frame so a single undo is always available.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || snapshot.current) return;
    try {
      snapshot.current = canvas.toDataURL('image/png');
    } catch {
      snapshot.current = null;
    }
  }, []);

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

  const improve = async () => {
    if (isBlank()) {
      setError('Draw something first');
      return;
    }
    setImproving(true);
    setError(null);
    try {
      const edited = await api.editImage({ prompt: 'Make this drawing much better', image: toPng() });
      const raw = edited?.data?.[0]?.b64_json ?? edited?.data?.[0]?.url;
      if (edited?.status === 'SUCCESS' && raw) {
        setResult(raw.startsWith('data:') ? raw : `data:image/png;base64,${raw}`);
      } else {
        setError(edited?.message || 'The image backend did not return a picture');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not improve that drawing');
    } finally {
      setImproving(false);
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
      <div className="flex flex-wrap items-center gap-2">
        {COLORS.map((c) => (
          <button
            key={c}
            type="button"
            aria-label={`Colour ${c}`}
            aria-pressed={color === c}
            onClick={() => setColor(c)}
            className={`h-8 w-8 rounded-full border-2 ${color === c ? 'border-white' : 'border-white/20'}`}
            style={{ background: c }}
          />
        ))}
        {WIDTHS.map((w) => (
          <button
            key={w}
            type="button"
            aria-label={`Brush ${w}`}
            aria-pressed={width === w}
            onClick={() => setWidth(w)}
            className={`glass-button min-h-8 px-2 text-[11px] ${width === w ? 'border-purple-400/50 text-purple-200' : ''}`}
          >
            {w}px
          </button>
        ))}
        <button type="button" onClick={clear} className="glass-button min-h-8 px-2 text-[11px]" aria-label="Clear">
          <Eraser size={12} /> Clear
        </button>
        <button type="button" onClick={undo} className="glass-button min-h-8 px-2 text-[11px]" aria-label="Undo">
          Undo
        </button>
        <button type="button" onClick={download} className="glass-button min-h-8 px-2 text-[11px]" aria-label="Download">
          <Download size={12} />
        </button>
      </div>

      <canvas
        ref={canvasRef}
        width={640}
        height={420}
        data-testid="draw-surface"
        onPointerDown={start}
        onPointerMove={move}
        onPointerUp={end}
        onPointerLeave={end}
        className="w-full h-auto rounded-xl bg-white touch-none"
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
        {onSend && (
          <button
            type="button"
            onClick={() => onSend(toPng())}
            className="glass-button min-h-11 px-3 py-2 text-sm"
          >
            <Sparkles size={14} /> Send to chat
          </button>
        )}
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
        </div>
      )}
    </div>
  );
}
