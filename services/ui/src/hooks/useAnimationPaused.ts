import { useCallback, useSyncExternalStore } from 'react';
import { animationChoice, setAnimationChoice, startsPaused, subscribeAnimationChoices } from '../lib/animatedImage';

const REDUCED_MOTION = '(prefers-reduced-motion: reduce)';

function reducedMotionQuery(): MediaQueryList | null {
  try {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(REDUCED_MOTION) : null;
  } catch {
    return null;
  }
}

/** Whether the person asked for less motion (the system setting), live. */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(
    useCallback((fn: () => void) => {
      const query = reducedMotionQuery();
      if (!query) return () => {};
      query.addEventListener?.('change', fn);
      return () => query.removeEventListener?.('change', fn);
    }, []),
    () => reducedMotionQuery()?.matches === true,
    () => false,
  );
}

/** This session's choice for one picture, shared by every place it is on screen (the bubble and the viewer). */
export function useAnimationPaused(mediaKey: string): [boolean, (paused: boolean) => void] {
  const choice = useSyncExternalStore(subscribeAnimationChoices, () => animationChoice(mediaKey), () => null);
  const reduced = useReducedMotion();
  const set = useCallback((paused: boolean) => setAnimationChoice(mediaKey, paused ? 'paused' : 'playing'), [mediaKey]);
  return [startsPaused(choice, reduced), set];
}
