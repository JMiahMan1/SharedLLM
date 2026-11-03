import { useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { useHaptics } from '../../hooks/useHaptics';

interface GamePlayerProps {
  title: string;
  /** Play page URL without the safe-area parameter; the player adds it. */
  src: string;
  frameRef: RefObject<HTMLIFrameElement | null>;
  onClose: () => void;
  emoji?: string;
  art?: { from?: string; to?: string } | null;
}

/** The phone's notch / home-bar insets in CSS px, measured once. */
function measureSafeArea(): string {
  const probe = document.createElement('div');
  probe.style.cssText =
    'position:fixed;visibility:hidden;pointer-events:none;' +
    'padding:env(safe-area-inset-top,0px) env(safe-area-inset-right,0px) env(safe-area-inset-bottom,0px) env(safe-area-inset-left,0px)';
  document.body.appendChild(probe);
  const style = getComputedStyle(probe);
  const sides = [style.paddingTop, style.paddingRight, style.paddingBottom, style.paddingLeft].map(
    (v) => Math.round(parseFloat(v) || 0),
  );
  probe.remove();
  return sides.join(',');
}

/**
 * Full-screen game, the way a native game runs.
 *
 * Edge to edge with no app chrome: the game draws behind the notch and the
 * home bar and keeps its own HUD clear of them (the insets are passed in,
 * because env() is 0 inside an iframe). A launch screen with the game's art
 * shows until the game says it is ready. Leaving is always one tap: the game
 * shows an ✕ in its top-left corner and Exit on its pause, title and result
 * cards; Android Back and Escape also close it. The game's haptics play
 * through the native layer, and the page behind is locked so a swipe in the
 * game can never scroll it.
 */
export default function GamePlayer({ title, src, frameRef, onClose, emoji, art }: GamePlayerProps) {
  const { trigger } = useHaptics();
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);
  const [ready, setReady] = useState(false);
  const frameSrc = useMemo(() => {
    const url = new URL(src, window.location.href);
    url.searchParams.set('safe', measureSafeArea());
    return url.toString();
  }, [src]);
  const origin = useMemo(() => new URL(src, window.location.href).origin, [src]);

  useEffect(() => {
    const { body } = document;
    const previous = { overflow: body.style.overflow, overscroll: body.style.overscrollBehavior };
    body.style.overflow = 'hidden';
    body.style.overscrollBehavior = 'none';
    return () => {
      body.style.overflow = previous.overflow;
      body.style.overscrollBehavior = previous.overscroll;
    };
  }, []);

  // Back closes the game instead of leaving the Family page: the player owns
  // one history entry, and every way of closing goes through history.back(),
  // so the popstate below is the single place the game actually closes.
  // (Pushing and popping in mount/cleanup broke under StrictMode's double
  // mount: the cleanup's back() closed the game the moment it opened.)
  useEffect(() => {
    if (!window.history.state?.familyGame) window.history.pushState({ familyGame: true }, '');
    const onPop = () => {
      if (!window.history.state?.familyGame) closeRef.current();
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  useEffect(() => {
    const close = () => {
      if (window.history.state?.familyGame) window.history.back();
      else closeRef.current();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    // The game's Exit, its haptics and its "ready" signal arrive relayed by
    // the arcade page.
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== origin) return;
      const message = event.data as { source?: string; type?: string; ms?: number } | null;
      if (message?.source !== 'arcade-relay') return;
      if (message.type === 'ready') setReady(true);
      else if (message.type === 'exit') close();
      else if (message.type === 'haptic') void trigger((message.ms ?? 0) >= 30 ? 'medium' : 'light');
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('message', onMessage);
    // A game that never says ready (an older page) is still shown.
    const fallback = window.setTimeout(() => setReady(true), 6000);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('message', onMessage);
      window.clearTimeout(fallback);
    };
  }, [origin, trigger]);

  const from = art?.from || '#7c5cff';
  const to = art?.to || '#ff7ab6';

  return createPortal(
    <div
      className="fixed inset-0 z-[1000] bg-black"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      data-testid="family-player"
      style={{ touchAction: 'none', overscrollBehavior: 'none' }}
    >
      <iframe
        ref={frameRef}
        src={frameSrc}
        title={`${title} game`}
        data-testid="family-player-frame"
        className="absolute inset-0 block h-full w-full border-0"
        allow="autoplay; fullscreen; gamepad"
        allowFullScreen
      />
      <div
        className={`absolute inset-0 flex flex-col items-center justify-center gap-3 transition-opacity duration-300 ${
          ready ? 'pointer-events-none opacity-0' : 'opacity-100'
        }`}
        style={{ backgroundImage: `linear-gradient(160deg, ${from}, ${to})` }}
        data-testid="family-player-splash"
        aria-hidden={ready}
      >
        <span className="animate-bounce text-7xl drop-shadow-lg">{emoji || '🎮'}</span>
        <span className="text-2xl font-black tracking-tight text-white drop-shadow">{title}</span>
        <span className="mt-2 h-1.5 w-24 overflow-hidden rounded-full bg-white/30">
          <span className="block h-full w-1/2 animate-pulse rounded-full bg-white/90" />
        </span>
        {/* While loading the game's own ✕ is not there yet. */}
        <button
          type="button"
          onClick={() => {
            if (window.history.state?.familyGame) window.history.back();
            else closeRef.current();
          }}
          aria-label="Close game"
          className="absolute left-[calc(10px+env(safe-area-inset-left))] top-[calc(8px+env(safe-area-inset-top))] flex h-11 w-11 items-center justify-center rounded-2xl bg-black/25 text-xl text-white backdrop-blur"
          data-testid="family-player-close"
        >
          ✕
        </button>
      </div>
    </div>,
    document.body,
  );
}
