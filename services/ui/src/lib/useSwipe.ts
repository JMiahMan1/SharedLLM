import { useRef } from 'react';
import type { TouchEvent } from 'react';

/**
 * Horizontal swipe for paging, the way a thumb turns a page in every reading
 * app. A swipe must be mostly sideways and long enough that a vertical scroll
 * with a little drift never turns the chapter.
 */
export function useSwipe(onLeft: () => void, onRight: () => void, threshold = 70) {
  const start = useRef<{ x: number; y: number } | null>(null);
  return {
    onTouchStart: (e: TouchEvent) => {
      const t = e.touches[0];
      start.current = t ? { x: t.clientX, y: t.clientY } : null;
    },
    onTouchEnd: (e: TouchEvent) => {
      const from = start.current;
      const t = e.changedTouches[0];
      start.current = null;
      if (!from || !t) return;
      const dx = t.clientX - from.x;
      const dy = t.clientY - from.y;
      if (Math.abs(dx) < threshold || Math.abs(dx) < Math.abs(dy) * 2) return;
      if (dx < 0) onLeft();
      else onRight();
    },
  };
}
