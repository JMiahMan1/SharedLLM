import { useRef, useState, useCallback } from 'react';

interface ImageViewerProps {
  url: string;
}

// Native <img> with wheel-zoom and drag-pan. Lightweight, mobile-friendly.
export function ImageViewer({ url }: ImageViewerProps) {
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const start = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);

  const onWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    setScale((s) => Math.min(8, Math.max(0.2, s * (e.deltaY < 0 ? 1.1 : 0.9))));
  }, []);

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    start.current = { x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y };
    setDragging(true);
    (e.target as Element).setPointerCapture?.(e.pointerId);
  }, [offset]);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!start.current) return;
    setOffset({
      x: start.current.ox + (e.clientX - start.current.x),
      y: start.current.oy + (e.clientY - start.current.y),
    });
  }, []);

  const onPointerUp = useCallback(() => {
    start.current = null;
    setDragging(false);
  }, []);

  return (
    <div
      className="relative h-full w-full overflow-hidden bg-[#0b0f1a] flex items-center justify-center touch-none"
      onWheel={onWheel}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerLeave={onPointerUp}
    >
      <img
        src={url}
        alt=""
        draggable={false}
        className="max-w-none select-none"
        style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`, transition: dragging ? 'none' : 'transform 0.05s' }}
      />
      <div className="absolute bottom-2 left-2 flex items-center gap-1 text-[11px] text-slate-300 bg-black/50 rounded px-2 py-1">
        <button className="px-1 hover:text-white" onClick={() => setScale((s) => Math.min(8, s * 1.2))}>+</button>
        <span className="w-10 text-center">{Math.round(scale * 100)}%</span>
        <button className="px-1 hover:text-white" onClick={() => setScale((s) => Math.max(0.2, s / 1.2))}>−</button>
        <button className="px-1 hover:text-white" onClick={() => { setScale(1); setOffset({ x: 0, y: 0 }); }}>reset</button>
      </div>
    </div>
  );
}
