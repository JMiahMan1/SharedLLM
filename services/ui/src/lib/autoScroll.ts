import { useCallback, useEffect, useRef, useState } from 'react';
import { holdScreenOn, type AwakeHold } from './keepAwake';

/**
 * Auto scroll for reading surfaces (the Bible reader, focus mode, Bible
 * studies and devotionals): the page moves up at a steady, adjustable pace so
 * a reader can follow along hands free, the way a teleprompter does.
 *
 * - Speeds are steps of pixels per second, slow enough for careful reading at
 *   the bottom and a skim at the top. The chosen step is remembered on this
 *   device.
 * - Touching, dragging or wheeling the page holds the scroll while the reader
 *   is in control and picks it up again a moment after they let go.
 * - It stops by itself at the end of the text, and keeps the screen on while
 *   it runs (a phone would otherwise lock mid-chapter).
 */

/** Pixels per second for each speed step. */
export const SPEEDS = [10, 15, 21, 28, 37, 48, 62, 80] as const;
export const DEFAULT_SPEED = 2;
/** How long after the reader lets go of the page the scroll picks up again. */
export const RESUME_AFTER_MS = 1500;
/** Longest frame gap honoured, so a stalled or hidden tab never lurches on return. */
const MAX_FRAME_MS = 100;
const SPEED_KEY = 'jarvis_auto_scroll_speed';

export function clampSpeed(index: number): number {
  return Math.min(SPEEDS.length - 1, Math.max(0, Math.round(index)));
}

export function readSpeed(): number {
  try {
    const raw = window.localStorage.getItem(SPEED_KEY);
    if (raw !== null && raw !== '' && Number.isFinite(Number(raw))) return clampSpeed(Number(raw));
  } catch {
    // storage blocked: the default pace still works
  }
  return DEFAULT_SPEED;
}

function saveSpeed(index: number) {
  try {
    window.localStorage.setItem(SPEED_KEY, String(index));
  } catch {
    // storage blocked: the pace lasts this visit only
  }
}

/** Whether the element is scrolled as far down as it goes. */
export function atBottom(el: Pick<HTMLElement, 'scrollTop' | 'clientHeight' | 'scrollHeight'>): boolean {
  return el.scrollTop + el.clientHeight >= el.scrollHeight - 2;
}

/**
 * Move ``el`` down by ``pxPerSecond`` over ``elapsedMs``. Browsers round
 * ``scrollTop``, so slow speeds would never move: the part of a pixel that did
 * not land is carried into the next frame. Returns the new carry.
 */
export function advance(el: HTMLElement, pxPerSecond: number, elapsedMs: number, carry: number): number {
  const before = el.scrollTop;
  const target = before + carry + (pxPerSecond * Math.min(elapsedMs, MAX_FRAME_MS)) / 1000;
  el.scrollTop = target;
  const moved = el.scrollTop - before;
  // Nothing moved at all and we asked for a whole pixel or more: the end, or a page that cannot scroll.
  if (moved <= 0 && target - before >= 1) return 0;
  return target - el.scrollTop;
}

export interface AutoScroll {
  running: boolean;
  /** Running, but held: the reader has the page, or the caller paused it. */
  held: boolean;
  /** The last run stopped because the text ended. */
  ended: boolean;
  speed: number;
  start(): void;
  stop(): void;
  toggle(): void;
  faster(): void;
  slower(): void;
}

/**
 * Drive auto scroll on whatever element ``target`` returns (looked up when a
 * run starts, so a surface that mounts later still works). ``paused`` holds
 * it from outside, for a sheet open over the text.
 */
export function useAutoScroll(target: () => HTMLElement | null, paused = false): AutoScroll {
  const [running, setRunning] = useState(false);
  const [ended, setEnded] = useState(false);
  const [speed, setSpeed] = useState(readSpeed);
  const [touching, setTouching] = useState(false);
  const targetRef = useRef(target);
  const speedRef = useRef(speed);
  const pausedRef = useRef(paused);
  const holdUntil = useRef(0);
  const touchingRef = useRef(false);

  useEffect(() => {
    targetRef.current = target;
  }, [target]);
  useEffect(() => {
    speedRef.current = speed;
  }, [speed]);
  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  const start = useCallback(() => {
    const el = targetRef.current();
    if (el && atBottom(el) && el.scrollHeight > el.clientHeight) {
      // Started at the end: begin again from the top rather than doing nothing.
      el.scrollTop = 0;
    }
    holdUntil.current = 0;
    setEnded(false);
    setRunning(true);
  }, []);
  const stop = useCallback(() => setRunning(false), []);
  const toggle = useCallback(() => {
    if (running) stop();
    else start();
  }, [running, start, stop]);
  const changeSpeed = useCallback((delta: number) => {
    setSpeed((current) => {
      const next = clampSpeed(current + delta);
      saveSpeed(next);
      return next;
    });
  }, []);
  const faster = useCallback(() => changeSpeed(1), [changeSpeed]);
  const slower = useCallback(() => changeSpeed(-1), [changeSpeed]);

  // The loop: one step per frame while running.
  useEffect(() => {
    if (!running) return undefined;
    const el = targetRef.current();
    if (!el) {
      setRunning(false);
      return undefined;
    }
    // A `scroll-behavior: smooth` container would animate every frame's step and fall behind.
    const previousBehavior = el.style.scrollBehavior;
    el.style.scrollBehavior = 'auto';
    let frame = 0;
    let last = performance.now();
    let carry = 0;
    const tick = (now: number) => {
      const elapsed = now - last;
      last = now;
      const holding = pausedRef.current || touchingRef.current || now < holdUntil.current;
      if (!holding) {
        carry = advance(el, SPEEDS[speedRef.current], elapsed, carry);
        if (atBottom(el)) {
          el.scrollTop = el.scrollHeight;  // settle on the very end
          setEnded(true);
          setRunning(false);
          return;
        }
      } else {
        carry = 0;
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);

    // The reader taking the page holds the scroll; letting go starts the resume clock.
    const grab = () => {
      touchingRef.current = true;
      setTouching(true);
    };
    const release = () => {
      if (!touchingRef.current) return;
      touchingRef.current = false;
      setTouching(false);
      holdUntil.current = performance.now() + RESUME_AFTER_MS;
    };
    const nudge = () => {
      holdUntil.current = performance.now() + RESUME_AFTER_MS;
    };
    const onKey = (event: KeyboardEvent) => {
      if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) nudge();
    };
    el.addEventListener('pointerdown', grab, { passive: true });
    el.addEventListener('touchstart', grab, { passive: true });
    window.addEventListener('pointerup', release, { passive: true });
    window.addEventListener('pointercancel', release, { passive: true });
    window.addEventListener('touchend', release, { passive: true });
    window.addEventListener('touchcancel', release, { passive: true });
    el.addEventListener('wheel', nudge, { passive: true });
    window.addEventListener('keydown', onKey);
    return () => {
      cancelAnimationFrame(frame);
      el.style.scrollBehavior = previousBehavior;
      el.removeEventListener('pointerdown', grab);
      el.removeEventListener('touchstart', grab);
      window.removeEventListener('pointerup', release);
      window.removeEventListener('pointercancel', release);
      window.removeEventListener('touchend', release);
      window.removeEventListener('touchcancel', release);
      el.removeEventListener('wheel', nudge);
      window.removeEventListener('keydown', onKey);
      touchingRef.current = false;
    };
  }, [running]);

  // Keep the screen on while it runs.
  useEffect(() => {
    if (!running) return undefined;
    let hold: AwakeHold | null = null;
    let cancelled = false;
    void holdScreenOn().then((h) => {
      if (cancelled) void h?.release().catch(() => undefined);
      else hold = h;
    });
    return () => {
      cancelled = true;
      void hold?.release().catch(() => undefined);
    };
  }, [running]);

  return { running, held: running && (paused || touching), ended, speed, start, stop, toggle, faster, slower };
}
