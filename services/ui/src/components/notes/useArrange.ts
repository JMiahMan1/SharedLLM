import { useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';

export interface Arranging {
  path: string;
  title: string;
  x: number;
  y: number;
  /** The card under the finger, which the held one will take the place of. */
  over: string | null;
}

/** How long a press has to last before it picks a card up (a quick drag still scrolls). */
const HOLD_MS = 350;

/**
 * Keep-style arranging: press and hold a card, drag it over another, let go.
 * `onDrop(from, to)` is called with the two paths; the page records the order.
 * Cards carry `data-note-path` so the card under the finger can be found.
 */
export function useArrange(enabled: boolean, onDrop: (from: string, to: string) => void) {
  const [arranging, setArranging] = useState<Arranging | null>(null);
  const hold = useRef<{ timer: number; x: number; y: number } | null>(null);
  const justDropped = useRef(false);
  const dropRef = useRef(onDrop);
  useEffect(() => {
    dropRef.current = onDrop;
  });

  const cancelHold = () => {
    if (hold.current) window.clearTimeout(hold.current.timer);
    hold.current = null;
  };

  useEffect(() => {
    if (!arranging) return;
    // While a card is held, the finger moves it instead of scrolling the page.
    const stopScroll = (e: TouchEvent) => e.preventDefault();
    const move = (e: PointerEvent) => {
      const target = (document.elementFromPoint(e.clientX, e.clientY) as HTMLElement | null)?.closest<HTMLElement>('[data-note-path]');
      const over = target?.dataset.notePath ?? null;
      setArranging((a) => (a ? { ...a, x: e.clientX, y: e.clientY, over: over === a.path ? null : over } : a));
    };
    const drop = () => {
      setArranging((a) => {
        if (a?.over) dropRef.current(a.path, a.over);
        return null;
      });
      justDropped.current = true;
      window.setTimeout(() => {
        justDropped.current = false;
      }, 50);
    };
    document.addEventListener('touchmove', stopScroll, { passive: false });
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', drop);
    window.addEventListener('pointercancel', drop);
    return () => {
      document.removeEventListener('touchmove', stopScroll);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', drop);
      window.removeEventListener('pointercancel', drop);
    };
  }, [arranging]);

  /** Props for one card. */
  const cardProps = (path: string, title: string) => ({
    'data-note-path': path,
    title: enabled ? 'Press and hold to move' : undefined,
    onPointerDown: (e: ReactPointerEvent) => {
      if (!enabled || e.button !== 0) return;
      const x = e.clientX;
      const y = e.clientY;
      cancelHold();
      const timer = window.setTimeout(() => {
        hold.current = null;
        setArranging({ path, title, x, y, over: null });
        if (navigator.vibrate) navigator.vibrate(12);
      }, HOLD_MS);
      hold.current = { timer, x, y };
    },
    onPointerMove: (e: ReactPointerEvent) => {
      const h = hold.current;
      if (h && Math.hypot(e.clientX - h.x, e.clientY - h.y) > 8) cancelHold();
    },
    onPointerUp: cancelHold,
    onPointerLeave: cancelHold,
    onContextMenu: (e: ReactPointerEvent | React.MouseEvent) => {
      if (enabled) e.preventDefault();
    },
  });

  /** True for the click that ends a drag, which must not open the note. */
  const swallowClick = () => justDropped.current;

  return { arranging, cardProps, swallowClick };
}
