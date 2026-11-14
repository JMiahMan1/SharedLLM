import { createPortal } from 'react-dom';
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type WheelEvent } from 'react';
import { Loader2, RotateCcw, ZoomIn, ZoomOut } from 'lucide-react';
import { CENTRED, MAX_ZOOM, MIN_ZOOM, clampCrop, cropRect, panned, zoomed, type AvatarCrop } from '../lib/avatarCrop';

interface AvatarCropperProps {
  /** The photo, as a URL an <img> can show (the kept original or the chosen file). */
  src: string;
  width: number;
  height: number;
  initial?: AvatarCrop | null;
  saving?: boolean;
  onCancel: () => void;
  onSave: (crop: AvatarCrop) => void;
}

/** How the photo sits behind a square view, as percentages of the view, so it needs no measuring. */
function photoStyle(crop: AvatarCrop, width: number, height: number) {
  const { left, top, side } = cropRect(crop, width, height);
  return {
    width: `${(width / side) * 100}%`,
    height: `${(height / side) * 100}%`,
    left: `${(-left / side) * 100}%`,
    top: `${(-top / side) * 100}%`,
  };
}

function CropPreview({ src, crop, width, height, size, label }: { src: string; crop: AvatarCrop; width: number; height: number; size: number; label: string }) {
  return (
    <figure className="flex flex-col items-center gap-1">
      <div className="relative overflow-hidden rounded-full border border-white/15 bg-slate-800" style={{ width: size, height: size }}>
        <img src={src} alt="" draggable={false} className="absolute max-w-none select-none" style={photoStyle(crop, width, height)} />
      </div>
      <figcaption className="text-[10px] text-slate-400">{label}</figcaption>
    </figure>
  );
}

const ROUND = 'flex min-h-11 min-w-11 items-center justify-center rounded-xl text-slate-200 hover:bg-white/10 disabled:opacity-30';

/**
 * Choose the part of a photo that becomes the profile picture: drag the photo
 * to move it, pinch, scroll or use the slider to zoom. The circle is what
 * everyone will see; the small previews show it at the sizes chat and the
 * family list use. Arrow keys move it and + / - zoom for a keyboard.
 */
export default function AvatarCropper({ src, width, height, initial, saving = false, onCancel, onSave }: AvatarCropperProps) {
  const [crop, setCrop] = useState<AvatarCrop>(() => clampCrop(initial ?? CENTRED, width, height));
  const viewRef = useRef<HTMLDivElement | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ distance: number; zoom: number } | null>(null);

  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);

  const viewSize = () => viewRef.current?.clientWidth || 1;
  const focal = (clientX: number, clientY: number) => {
    const box = viewRef.current?.getBoundingClientRect();
    if (!box || !box.width) return { fx: 0.5, fy: 0.5 };
    return { fx: (clientX - box.left) / box.width, fy: (clientY - box.top) / box.height };
  };

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture?.(event.pointerId);
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      pinch.current = { distance: Math.hypot(a.x - b.x, a.y - b.y) || 1, zoom: crop.zoom };
    }
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const last = pointers.current.get(event.pointerId);
    if (!last) return;
    const now = { x: event.clientX, y: event.clientY };
    pointers.current.set(event.pointerId, now);
    if (pointers.current.size >= 2 && pinch.current) {
      const [a, b] = [...pointers.current.values()];
      const ratio = (Math.hypot(a.x - b.x, a.y - b.y) || 1) / pinch.current.distance;
      const { fx, fy } = focal((a.x + b.x) / 2, (a.y + b.y) / 2);
      setCrop((current) => zoomed(current, pinch.current!.zoom * ratio, width, height, fx, fy));
      return;
    }
    setCrop((current) => panned(current, now.x - last.x, now.y - last.y, viewSize(), width, height));
  };
  const onPointerUp = (event: PointerEvent<HTMLDivElement>) => {
    pointers.current.delete(event.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
  };
  const onWheel = (event: WheelEvent<HTMLDivElement>) => {
    const { fx, fy } = focal(event.clientX, event.clientY);
    const factor = Math.exp(-event.deltaY * 0.0015);
    setCrop((current) => zoomed(current, current.zoom * factor, width, height, fx, fy));
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = 12;
    const moves: Record<string, [number, number]> = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
    if (moves[event.key]) {
      event.preventDefault();
      const [dx, dy] = moves[event.key];
      setCrop((current) => panned(current, dx, dy, viewSize(), width, height));
    } else if (event.key === '+' || event.key === '=') {
      event.preventDefault();
      setCrop((current) => zoomed(current, current.zoom + 0.2, width, height));
    } else if (event.key === '-' || event.key === '_') {
      event.preventDefault();
      setCrop((current) => zoomed(current, current.zoom - 0.2, width, height));
    }
  };
  const zoomTo = (zoom: number) => setCrop((current) => zoomed(current, zoom, width, height));

  // In the body, for the same reason as the picker: no frosted card around it may frame or cover it.
  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-end sm:items-center sm:justify-center" role="dialog" aria-modal="true" aria-label="Crop your photo" data-testid="avatar-cropper">
      <button type="button" aria-label="Cancel" onClick={onCancel} className="absolute inset-0 bg-black/70 backdrop-blur-sm" />
      <div className="relative flex max-h-[100dvh] w-full flex-col overflow-y-auto rounded-t-2xl border border-white/10 bg-slate-950 p-4 pb-[max(1rem,env(safe-area-inset-bottom))] sm:max-w-lg sm:rounded-2xl sm:p-5">
        <h2 className="text-base font-bold text-white">Crop your photo</h2>
        <p className="text-xs text-slate-400">Drag to move it. Pinch, scroll or use the slider to zoom. The circle is what everyone sees.</p>

        <div className="mt-3 flex justify-center">
          <div
            ref={viewRef}
            role="application"
            aria-label="Photo. Use the arrow keys to move it, plus and minus to zoom."
            tabIndex={0}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            onWheel={onWheel}
            onKeyDown={onKeyDown}
            className="relative aspect-square w-full max-w-[min(100%,22rem,52dvh)] cursor-grab touch-none select-none overflow-hidden rounded-xl bg-slate-900 outline-none focus-visible:ring-2 focus-visible:ring-purple-400 active:cursor-grabbing"
            data-testid="avatar-crop-view"
          >
            <img src={src} alt="" draggable={false} className="pointer-events-none absolute max-w-none" style={photoStyle(crop, width, height)} />
            {/* Outside the circle is dimmed: that part will not show. */}
            <div className="pointer-events-none absolute inset-0 rounded-full shadow-[0_0_0_9999px_rgba(2,6,23,0.6)] ring-2 ring-white/80" aria-hidden />
          </div>
        </div>

        <div className="mt-3 flex items-center gap-1">
          <button type="button" onClick={() => zoomTo(crop.zoom - 0.25)} disabled={crop.zoom <= MIN_ZOOM} aria-label="Zoom out" className={ROUND}>
            <ZoomOut size={18} />
          </button>
          <input
            type="range"
            min={MIN_ZOOM}
            max={MAX_ZOOM}
            step={0.01}
            value={crop.zoom}
            onChange={(e) => zoomTo(Number(e.target.value))}
            aria-label="Zoom"
            className="min-h-11 min-w-0 flex-1 accent-purple-500"
            data-testid="avatar-crop-zoom"
          />
          <button type="button" onClick={() => zoomTo(crop.zoom + 0.25)} disabled={crop.zoom >= MAX_ZOOM} aria-label="Zoom in" className={ROUND}>
            <ZoomIn size={18} />
          </button>
          <button type="button" onClick={() => setCrop(clampCrop(CENTRED, width, height))} aria-label="Reset the crop" className={ROUND}>
            <RotateCcw size={17} />
          </button>
        </div>

        <div className="mt-2 flex items-end justify-center gap-5" aria-label="How it will look" data-testid="avatar-crop-previews">
          <CropPreview src={src} crop={crop} width={width} height={height} size={72} label="Profile" />
          <CropPreview src={src} crop={crop} width={width} height={height} size={40} label="Chat" />
          <CropPreview src={src} crop={crop} width={width} height={height} size={28} label="Lists" />
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2">
          <button type="button" onClick={onCancel} disabled={saving} className="min-h-11 rounded-xl border border-white/10 text-sm text-slate-300 disabled:opacity-60">
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onSave(crop)}
            disabled={saving}
            className="flex min-h-11 items-center justify-center gap-2 rounded-xl bg-purple-600 text-sm font-semibold text-white disabled:opacity-60"
            data-testid="avatar-crop-save"
          >
            {saving ? <Loader2 size={15} className="animate-spin" /> : null}
            Save photo
          </button>
        </div>
      </div>
    </div>
  , document.body);
}
