import { useEffect, useId, useState } from 'react';
import { useReducedMotion } from 'framer-motion';
import { VOICE_BARS, faceFor, type FaceState } from './jarvisFace';

/**
 * Jarvis's head.
 *
 * The shield from the app icon is his face, so he is the same character on the
 * launcher, in the assistant sheet and on a wall tablet. Inside the shield sits
 * a visor with two eyes and a mouth; the mouth becomes five voice bars while he
 * talks. Every number comes from `jarvisFace.ts`, which is why the watch and the
 * ESPHome display can wear the same expression from the same code.
 *
 * Draws at any size: the shapes are all relative to a 120-unit box, so 32 px in
 * a list and 240 px on a tablet are the same drawing.
 */
interface Props {
  state: FaceState;
  /** Microphone level, 0..100. Only listening and speaking use it. */
  level?: number;
  /** Rendered size in px. */
  size?: number;
  /** Fewer details for very small sizes (launcher, list rows). */
  compact?: boolean;
  className?: string;
}

/** The shield outline from `public/jarvis-icon.svg`, in a 120-unit box. */
const SHIELD = 'M60 6 L104 21 L104 62 C104 89 82 108 60 116 C38 108 16 89 16 62 L16 21 Z';

export default function JarvisHead({ state, level = 0, size = 176, compact = false, className }: Props) {
  const reduced = useReducedMotion();
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const [tick, setTick] = useState(0);

  // 30 fps is enough for a face and a third of the battery cost of 60. A person
  // who asked for less motion gets one still frame instead of a loop.
  useEffect(() => {
    if (reduced) return;
    let raf = 0;
    let last = 0;
    const started = performance.now();
    const step = (now: number) => {
      if (now - last >= 33) {
        last = now;
        setTick(now - started);
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [reduced]);

  const face = faceFor(state, level, reduced ? 0 : tick);
  const warm = face.accent === 'amber';
  const accent = warm ? '#f59e0b' : 'var(--site-accent, #8b5cf6)';
  const deep = warm ? '#b45309' : 'color-mix(in srgb, var(--site-accent, #8b5cf6) 55%, #4f46e5)';

  const eyes = [
    { cx: 46, cy: 53 },
    { cx: 74, cy: 53 },
  ];
  const eyeRy = Math.max(0.6, 9 * face.eyeOpen);
  const pupilX = face.lookX * 3.2;
  const pupilY = -face.lookY * 2.4;

  // The mouth: a curve whose sag is the smile, fattened by how open it is.
  const mouthHalf = 11 + face.mouthCurve * 2;
  const mouthSag = face.mouthCurve * 5.5;
  const mouthWidth = 2.6 + face.mouthOpen * 4.5;

  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 120 120"
      role="img"
      aria-label={`Jarvis, ${face.label}`}
      data-face-state={face.state}
      data-eye-open={face.eyeOpen.toFixed(2)}
      style={{ display: 'block', overflow: 'visible' }}
    >
      <defs>
        <linearGradient id={`${uid}-shield`} x1="16" y1="6" x2="104" y2="116" gradientUnits="userSpaceOnUse">
          <stop stopColor={accent} />
          <stop offset="1" stopColor={deep} />
        </linearGradient>
        <linearGradient id={`${uid}-visor`} x1="26" y1="36" x2="94" y2="72" gradientUnits="userSpaceOnUse">
          <stop stopColor="#0b0d12" />
          <stop offset="1" stopColor="#141a26" />
        </linearGradient>
        <filter id={`${uid}-soft`} x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="3" />
        </filter>
      </defs>

      {/* the glow he gives off: the only light in the room */}
      {!compact && (
        <path d={SHIELD} fill={accent} opacity={face.glow * 0.55} filter={`url(#${uid}-soft)`} />
      )}

      {/* the head */}
      <g transform={`rotate(${face.tilt} 60 60) translate(0 ${face.bob * 40})`}>
        <path d={SHIELD} fill={`url(#${uid}-shield)`} stroke="rgba(255,255,255,0.18)" strokeWidth={1.4} />
        {!compact && (
          <path
            d={SHIELD}
            fill="none"
            stroke="rgba(255,255,255,0.28)"
            strokeWidth={1}
            transform="translate(60 60) scale(0.9) translate(-60 -60)"
          />
        )}

        {/* the visor */}
        <rect x={26} y={36} width={68} height={34} rx={16} fill={`url(#${uid}-visor)`} opacity={0.96} />

        {eyes.map(({ cx, cy }, i) =>
          face.squint > 0.5 ? (
            // happy eyes are curved shut, like a person smiling with their eyes
            <path
              key={`eye-${cx}`}
              d={`M${cx - 7} ${cy + 1} Q${cx} ${cy - 6} ${cx + 7} ${cy + 1}`}
              fill="none"
              stroke={accent}
              strokeWidth={3.2}
              strokeLinecap="round"
            />
          ) : (
            <g key={`eye-${cx}`}>
              <ellipse cx={cx} cy={cy} rx={8} ry={eyeRy} fill={accent} opacity={0.95} />
              {!compact && eyeRy > 3 && (
                <circle cx={cx + pupilX} cy={cy + pupilY} r={3.1} fill="#0b0d12" opacity={0.85} />
              )}
              {i === 0 && !compact && (
                <ellipse cx={cx - 2.4} cy={cy - eyeRy * 0.35} rx={2} ry={1.6} fill="#ffffff" opacity={0.5} />
              )}
            </g>
          ),
        )}

        {/* speaking replaces the mouth with the sound itself */}
        {face.bars.length === VOICE_BARS ? (
          <g>
            {face.bars.map((bar, i) => {
              const h = 3 + bar * 13;
              const x = 60 + (i - (VOICE_BARS - 1) / 2) * 8;
              return (
                <rect
                  key={`bar-${i}`}
                  x={x - 2}
                  y={91 - h / 2}
                  width={4}
                  height={h}
                  rx={2}
                  fill={accent}
                  opacity={0.85 + bar * 0.15}
                />
              );
            })}
          </g>
        ) : (
          <path
            d={`M${60 - mouthHalf} 84 Q60 ${84 + mouthSag} ${60 + mouthHalf} 84`}
            fill="none"
            stroke={accent}
            strokeWidth={mouthWidth}
            strokeLinecap="round"
            opacity={0.9}
          />
        )}
      </g>
    </svg>
  );
}
