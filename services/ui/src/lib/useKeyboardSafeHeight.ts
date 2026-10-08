import { useEffect, useState, type RefObject } from 'react';

/**
 * A height that keeps `ref`'s element above the on-screen keyboard.
 *
 * Chrome and Android WebView shrink the page for the keyboard (the viewport
 * meta asks for `interactive-widget=resizes-content`); iOS Safari does not, and
 * covered the chat's input while typing. This follows the visual viewport and,
 * while it is noticeably smaller than the window (a keyboard is up), returns
 * the space left between the element's top and the keyboard. Otherwise it
 * returns null and the element keeps its normal size.
 */
export function useKeyboardSafeHeight(ref: RefObject<HTMLElement | null>, margin = 8): number | null {
  const [height, setHeight] = useState<number | null>(null);

  useEffect(() => {
    const vv = typeof window !== 'undefined' ? window.visualViewport : null;
    if (!vv) return;
    const update = () => {
      const el = ref.current;
      const keyboardUp = vv.height < window.innerHeight * 0.8;
      if (!el || !keyboardUp) {
        setHeight(null);
        return;
      }
      const top = el.getBoundingClientRect().top;
      setHeight(Math.max(200, Math.floor(vv.height + vv.offsetTop - top - margin)));
    };
    vv.addEventListener('resize', update);
    vv.addEventListener('scroll', update);
    return () => {
      vv.removeEventListener('resize', update);
      vv.removeEventListener('scroll', update);
    };
  }, [ref, margin]);

  return height;
}
