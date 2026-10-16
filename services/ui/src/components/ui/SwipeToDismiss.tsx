import { useRef, useState, type PointerEvent, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { useHaptics } from '../../hooks/useHaptics';

/** Share of the row's width a drag must pass to clear it. */
const DISMISS_FRACTION = 0.35;
/** A quick flick clears it whatever the distance (px per ms). */
const FLICK_SPEED = 0.6;
/** Movement before the gesture picks an axis, so a scroll with drift never swipes. */
const AXIS_SLOP = 8;
const SLIDE_MS = 180;

interface Props {
  onDismiss: () => void;
  /** Names the item for the × button, e.g. the notice's title. */
  label: string;
  className?: string;
  children: ReactNode;
}

/**
 * A list row that clears with a sideways swipe, like a notification in the
 * phone's shade, and with its × button for a mouse, keyboard or screen reader.
 * Vertical drags are left to the list so it still scrolls.
 */
export default function SwipeToDismiss({ onDismiss, label, className = '', children }: Props) {
  const { trigger } = useHaptics();
  const [dx, setDx] = useState(0);
  const [leaving, setLeaving] = useState<0 | 1 | -1>(0);
  // Set when a sideways drag starts: the row follows the finger with no easing.
  const [dragging, setDragging] = useState(false);
  const [width, setWidth] = useState(400);
  const rowRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; t: number; axis: 'x' | 'y' | null; id: number } | null>(null);

  const reducedMotion = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true;

  const clear = (direction: 1 | -1) => {
    void trigger('light');
    if (reducedMotion) {
      onDismiss();
      return;
    }
    setLeaving(direction);
    window.setTimeout(onDismiss, SLIDE_MS);
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    drag.current = { x: e.clientX, y: e.clientY, t: e.timeStamp, axis: null, id: e.pointerId };
  };

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    const mx = e.clientX - d.x;
    const my = e.clientY - d.y;
    if (!d.axis) {
      if (Math.abs(mx) < AXIS_SLOP && Math.abs(my) < AXIS_SLOP) return;
      d.axis = Math.abs(mx) > Math.abs(my) ? 'x' : 'y';
      if (d.axis === 'x') {
        e.currentTarget.setPointerCapture(e.pointerId);
        setWidth(rowRef.current?.offsetWidth || 400);
        setDragging(true);
      }
    }
    if (d.axis === 'x') setDx(mx);
  };

  const onPointerEnd = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    drag.current = null;
    if (!d || d.axis !== 'x') return;
    setDragging(false);
    const speed = Math.abs(dx) / Math.max(1, e.timeStamp - d.t);
    if (Math.abs(dx) > width * DISMISS_FRACTION || speed > FLICK_SPEED) clear(dx > 0 ? 1 : -1);
    else setDx(0);
  };

  const offset = leaving ? `${leaving * 110}%` : `${dx}px`;

  return (
    <div className={`relative overflow-hidden rounded-xl ${className}`}>
      {/* What the row slides off to reveal. */}
      <div
        aria-hidden
        className={`absolute inset-0 flex items-center px-4 text-xs font-semibold text-slate-400 ${dx < 0 ? 'justify-end' : 'justify-start'}`}
        style={{ opacity: Math.min(1, Math.abs(dx) / 60) }}
      >
        Clear
      </div>
      <div
        ref={rowRef}
        data-testid="swipe-row"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
        className="relative touch-pan-y select-none"
        style={{
          transform: `translateX(${offset})`,
          opacity: leaving ? 0 : 1 - Math.min(0.5, Math.abs(dx) / (width * 2)),
          transition: dragging ? 'none' : `transform ${SLIDE_MS}ms ease-out, opacity ${SLIDE_MS}ms ease-out`,
        }}
      >
        {children}
        <button
          type="button"
          aria-label={`Clear ${label}`}
          onClick={() => clear(1)}
          onPointerDown={(e) => e.stopPropagation()}
          className="absolute top-0.5 right-0.5 flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 hover:text-white hover:bg-white/10 transition-colors"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}
