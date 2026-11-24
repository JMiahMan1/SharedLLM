import { useEffect, useLayoutEffect, useRef, useState, type ImgHTMLAttributes, type ReactNode } from 'react';
import { Pause, Play } from 'lucide-react';
import { animatesAt, mayAnimate } from '../../lib/animatedImage';
import { useAnimationPaused } from '../../hooks/useAnimationPaused';

/** Keeps a press on the control from also opening the picture, the message's sheet or a long press. */
const keep = (event: { stopPropagation: () => void }) => event.stopPropagation();

/**
 * The pause and play control over an animated picture: a 44px target, a
 * labelled toggle (aria-pressed is "paused"), and a "GIF" tag while paused so
 * a still picture still says it moves.
 */
export function AnimationToggle({
  paused,
  onToggle,
  className = 'absolute left-1.5 top-1.5',
  testId = 'animation-toggle',
}: {
  paused: boolean;
  onToggle: () => void;
  className?: string;
  testId?: string;
}) {
  return (
    <button
      type="button"
      aria-label="Pause animation"
      aria-pressed={paused}
      title={paused ? 'Play' : 'Pause'}
      data-testid={testId}
      onClick={(event) => {
        event.stopPropagation();
        event.preventDefault();
        onToggle();
      }}
      onKeyDown={keep}
      onPointerDown={keep}
      onTouchStart={keep}
      onContextMenu={keep}
      className={`${className} z-10 flex min-h-11 min-w-11 items-center justify-center rounded-full outline-none focus-visible:ring-2 focus-visible:ring-white/90`}
    >
      <span
        className={`flex h-8 items-center justify-center gap-1 rounded-full bg-black/60 text-white shadow-lg ring-1 ring-white/30 backdrop-blur-sm transition-colors hover:bg-black/75 ${
          paused ? 'px-2.5' : 'w-8'
        }`}
      >
        {paused ? (
          <>
            <Play size={13} fill="currentColor" aria-hidden />
            <span className="text-[11px] font-bold tracking-wide" aria-hidden>
              GIF
            </span>
          </>
        ) : (
          <Pause size={13} fill="currentColor" aria-hidden />
        )}
      </span>
    </button>
  );
}

type ImgExtras = Omit<ImgHTMLAttributes<HTMLImageElement>, 'src' | 'alt' | 'className'> & { 'data-testid'?: string };

/**
 * A picture that may animate (GIF, animated WebP, APNG) with a pause control.
 *
 * Pausing freezes the frame on screen: the picture is drawn onto a canvas and
 * the canvas is shown in its place (an <img> GIF cannot be paused). Playing
 * shows the animated picture again. The choice is per picture (`mediaKey`, a
 * message or file id, never the object URL) and lasts the session. A picture
 * that does not animate is a plain <img> with no control. Nothing is fetched:
 * `src` is an object URL, read in memory to see whether it animates.
 */
export default function AnimatedImage({
  src,
  mediaKey,
  mime,
  alt,
  className = '',
  imgProps,
  wrap,
  wrapperClassName = 'relative block w-fit max-w-full',
  toggleClassName,
}: {
  src: string;
  mediaKey: string;
  mime?: string;
  alt: string;
  className?: string;
  imgProps?: ImgExtras;
  /** Puts the picture inside something (the button that opens it); the control stays outside, a sibling. */
  wrap?: (media: ReactNode) => ReactNode;
  wrapperClassName?: string;
  toggleClassName?: string;
}) {
  const maybe = mayAnimate(mime);
  const [animated, setAnimated] = useState<{ src: string; yes: boolean } | null>(null);
  const [paused, setPaused] = useAnimationPaused(mediaKey);
  const [loaded, setLoaded] = useState<string | null>(null);
  const image = useRef<HTMLImageElement | null>(null);
  const still = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    if (!maybe || !src) return;
    let live = true;
    void animatesAt(mediaKey, src).then((yes) => {
      if (live) setAnimated({ src, yes });
    });
    return () => {
      live = false;
    };
  }, [maybe, mediaKey, src]);

  const moves = maybe && animated?.src === src && animated.yes;
  const known = !maybe || animated?.src === src;
  const showStill = moves && paused;
  // Paused (or reduced motion) and not yet known to animate: show nothing moving until it is known.
  const holding = maybe && !known && paused;

  // Freeze the frame that is on screen now: drawn from the live picture as it is hidden.
  useLayoutEffect(() => {
    if (!showStill || loaded !== src) return;
    const img = image.current;
    const canvas = still.current;
    if (!img || !canvas || !img.naturalWidth) return;
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    try {
      canvas.getContext('2d')?.drawImage(img, 0, 0);
    } catch {
      // A picture that cannot be drawn stays as the empty frame; the control still plays it.
    }
  }, [showStill, loaded, src]);

  const media = (
    <>
      <img
        {...imgProps}
        ref={image}
        src={src}
        alt={alt}
        aria-hidden={showStill || undefined}
        onLoad={(event) => {
          setLoaded(src);
          imgProps?.onLoad?.(event);
        }}
        className={`${className} ${holding ? 'opacity-0' : ''}`}
        style={showStill ? { ...imgProps?.style, display: 'none' } : imgProps?.style}
      />
      {showStill && (
        <canvas
          ref={still}
          role="img"
          aria-label={alt}
          data-testid="animation-still"
          onContextMenu={imgProps?.onContextMenu as never}
          className={className}
        />
      )}
    </>
  );

  if (!maybe) return <>{wrap ? wrap(media) : media}</>;
  // The same elements whether or not it turns out to animate, so finding out does not reload the picture.
  return (
    <span className={wrapperClassName} data-animated={moves ? (paused ? 'paused' : 'playing') : undefined}>
      {wrap ? wrap(media) : media}
      {moves && <AnimationToggle paused={paused} onToggle={() => setPaused(!paused)} className={toggleClassName} />}
    </span>
  );
}
