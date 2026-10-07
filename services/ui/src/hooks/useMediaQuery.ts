import { useEffect, useState } from 'react';

/**
 * The width below which the Bible reader uses its phone layout.
 *
 * Kept as a query string rather than a number so the hook and any CSS that
 * mirrors it cannot drift apart by one pixel.
 */
export const PHONE_QUERY = '(max-width: 767px)';

function matches(query: string): boolean {
  // jsdom has no matchMedia, and a browser can refuse a malformed query, so
  // this reports "no match" rather than throwing during render. On a device
  // that cannot answer, the desktop layout is the safe reading: it is the one
  // with every control visible rather than hidden behind a button.
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false;
  }
  try {
    return window.matchMedia(query).matches;
  } catch {
    return false;
  }
}

/**
 * Whether a media query currently matches, and keeps up when it changes.
 *
 * The Bible reader's phone and desktop layouts are different shapes rather
 * than one restyle, so the component asks which applies instead of rendering
 * both and hiding one with CSS. That keeps a single copy of every control in
 * the document -- one identity per control, so nothing can be found twice.
 */
export function useMediaQuery(query: string): boolean {
  const [isMatch, setIsMatch] = useState(() => matches(query));

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return;
    }
    let mq: MediaQueryList;
    try {
      mq = window.matchMedia(query);
    } catch {
      return;
    }
    const update = () => setIsMatch(mq.matches);
    // Re-read on subscribe: the query may have started matching between the
    // first render and this effect, and that change would otherwise be missed.
    update();
    mq.addEventListener('change', update);
    return () => mq.removeEventListener('change', update);
  }, [query]);

  return isMatch;
}
