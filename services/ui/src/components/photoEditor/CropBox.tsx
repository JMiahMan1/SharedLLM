import { useRef, type KeyboardEvent, type PointerEvent } from 'react';
import { moveCrop, resizeCrop, type Corner } from '../../lib/photoEdit/geometry';
import type { CropRect } from '../../lib/photoEdit/recipe';

const CORNERS: ReadonlyArray<{ id: Corner; label: string; className: string }> = [
  { id: 'nw', label: 'Resize the crop from the top left corner', className: '-left-[18px] -top-[18px]' },
  { id: 'ne', label: 'Resize the crop from the top right corner', className: '-right-[18px] -top-[18px]' },
  { id: 'sw', label: 'Resize the crop from the bottom left corner', className: '-bottom-[18px] -left-[18px]' },
  { id: 'se', label: 'Resize the crop from the bottom right corner', className: '-bottom-[18px] -right-[18px]' },
];

/** Keyboard steps, as fractions of the picture. */
const STEP = 0.02;

/**
 * The crop box over the (uncropped) picture: drag inside to move it, drag a
 * corner to resize it (keeping the chosen shape), or use the arrow keys on
 * the box or a corner. Every handle is a 44px target.
 */
export default function CropBox({
  rect,
  ratio,
  pictureWidth,
  pictureHeight,
  boxWidth,
  boxHeight,
  onChange,
}: {
  rect: CropRect;
  /** Width/height in pixels, or null for free. */
  ratio: number | null;
  /** The oriented picture's own size (for the ratio). */
  pictureWidth: number;
  pictureHeight: number;
  /** The size it is shown at, in CSS pixels. */
  boxWidth: number;
  boxHeight: number;
  onChange: (rect: CropRect) => void;
}) {
  const drag = useRef<{ id: number; kind: 'move' | Corner; x: number; y: number; start: CropRect } | null>(null);

  const kindOf = (el: HTMLElement): 'move' | Corner => (el.dataset.cropKind as 'move' | Corner | undefined) ?? 'move';

  const begin = (event: PointerEvent<HTMLElement>) => {
    event.stopPropagation();
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    drag.current = { id: event.pointerId, kind: kindOf(event.currentTarget), x: event.clientX, y: event.clientY, start: rect };
  };
  const move = (event: PointerEvent<HTMLElement>) => {
    const d = drag.current;
    if (!d || d.id !== event.pointerId) return;
    event.stopPropagation();
    const dx = (event.clientX - d.x) / boxWidth;
    const dy = (event.clientY - d.y) / boxHeight;
    onChange(d.kind === 'move' ? moveCrop(d.start, dx, dy) : resizeCrop(d.start, d.kind, dx, dy, ratio, pictureWidth, pictureHeight));
  };
  const end = (event: PointerEvent<HTMLElement>) => {
    if (drag.current?.id === event.pointerId) drag.current = null;
  };
  const keys = (event: KeyboardEvent<HTMLElement>) => {
    const step: Record<string, [number, number]> = { ArrowLeft: [-STEP, 0], ArrowRight: [STEP, 0], ArrowUp: [0, -STEP], ArrowDown: [0, STEP] };
    const d = step[event.key];
    if (!d) return;
    event.preventDefault();
    const kind = kindOf(event.currentTarget);
    onChange(kind === 'move' ? moveCrop(rect, d[0], d[1]) : resizeCrop(rect, kind, d[0], d[1], ratio, pictureWidth, pictureHeight));
  };

  return (
    <div
      className="absolute touch-none border-2 border-white shadow-[0_0_0_9999px_rgba(0,0,0,0.55)]"
      style={{ left: rect.x * boxWidth, top: rect.y * boxHeight, width: rect.w * boxWidth, height: rect.h * boxHeight }}
      data-testid="photo-editor-crop-box"
    >
      <div
        role="button"
        tabIndex={0}
        aria-label="Move the crop (arrow keys move it)"
        className="absolute inset-0 cursor-move touch-none bg-[linear-gradient(to_right,transparent_33%,rgba(255,255,255,0.35)_33%,rgba(255,255,255,0.35)_calc(33%+1px),transparent_calc(33%+1px),transparent_66%,rgba(255,255,255,0.35)_66%,rgba(255,255,255,0.35)_calc(66%+1px),transparent_calc(66%+1px))]"
        data-crop-kind="move"
        onPointerDown={begin}
        onPointerMove={move}
        onPointerUp={end}
        onPointerCancel={end}
        onKeyDown={keys}
      />
      {CORNERS.map((c) => (
        <button
          key={c.id}
          type="button"
          aria-label={c.label}
          data-testid={`photo-editor-crop-${c.id}`}
          className={`absolute flex h-11 w-11 touch-none items-center justify-center ${c.className}`}
          data-crop-kind={c.id}
          onPointerDown={begin}
          onPointerMove={move}
          onPointerUp={end}
          onPointerCancel={end}
          onKeyDown={keys}
        >
          <span className="h-4 w-4 rounded-full border-2 border-slate-900 bg-white" aria-hidden />
        </button>
      ))}
    </div>
  );
}
