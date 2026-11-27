import { useEffect, useId, useState } from 'react';
import { useReducedMotion } from 'framer-motion';
import { THINKING_DOTS, VOICE_BARS, faceFor, type FaceState } from './jarvisFace';

/**
 * Jarvis's head.
 *
 * A rounded companion head - dark slate shell, glowing accent rim, two ear-pods
 * and a full-face dark glass visor - not the heraldic shield of the first
 * version. The pointy shield read as a security badge; this reads as something
 * that looks at you.
 *
 * **The visor is the whole face.** The eyes, the brows, the mouth, the speaking
 * bars and the thinking dots are all drawn on the dark glass, never on the
 * accent-coloured shell. That is a correctness rule, not a style: in v1 the
 * mouth and the voice bars were accent-on-accent and vanished on a real phone.
 *
 * Every number comes from `jarvisFace.ts`, which is why the watch, the tablet
 * and the ESPHome display wear the same expression from the same code.
 *
 * Draws at any size: the shapes are all relative to a 120-unit box, so 24 px in
 * a list and 240 px on a tablet are the same drawing.
 */
interface Props {
  state: FaceState;
  /** Microphone level, 0..100. Drives the ear-pods and the attentive mouth. */
  level?: number;
  /** Rendered size in px. */
  size?: number;
  /** Fewer details for very small sizes (launcher, list rows, tray icon). */
  compact?: boolean;
  className?: string;
}

/** The head shell, in a 120-unit box: a squircle, wider than it is tall. */
const SHELL = { x: 14, y: 18, w: 92, h: 84, r: 36 };
/** The full-face visor: glasses **and** mouth are inside it, so every light has contrast. */
const VISOR = { x: 22, y: 32, w: 76, h: 62, r: 26 };
/** Left and right ear-pods, tucked into the shell's sides. */
const POD = { w: 14, h: 30, y: 46, r: 7 };
const EYES = [
  { cx: 46, cy: 54 },
  { cx: 74, cy: 54 },
];

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

  const eyeRy = Math.max(0.6, 8.6 * face.eyeOpen);
  const pupilX = face.lookX * 3.2;
  const pupilY = -face.lookY * 2.4;

  // The mouth: a curve whose sag is the smile, fattened by how open it is.
  const mouthHalf = 11 + face.mouthCurve * 2;
  const mouthSag = face.mouthCurve * 5.5;
  const mouthWidth = 2.6 + face.mouthOpen * 4.2;

  // Brows sit above the eyes; -1 lifts the inner ends, +1 drops them.
  const browLift = face.brow * 2.2;
  const showBrow = Math.abs(face.brow) > 0.15;

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
      data-pod={face.pod.toFixed(2)}
      style={{ display: 'block', overflow: 'visible' }}
    >
      <defs>
        <linearGradient id={`${uid}-shell`} x1="20" y1="16" x2="100" y2="104" gradientUnits="userSpaceOnUse">
          <stop stopColor="#1c2331" />
          <stop offset="1" stopColor="#0b0e14" />
        </linearGradient>
        <linearGradient id={`${uid}-visor`} x1="22" y1="32" x2="98" y2="94" gradientUnits="userSpaceOnUse">
          <stop stopColor="#0b0d12" />
          <stop offset="1" stopColor="#131926" />
        </linearGradient>
        <filter id={`${uid}-soft`} x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="3.2" />
        </filter>
      </defs>

      {/* The light he gives off: the only light in the room. */}
      {!compact && (
        <rect
          x={SHELL.x}
          y={SHELL.y}
          width={SHELL.w}
          height={SHELL.h}
          rx={SHELL.r}
          fill={accent}
          opacity={face.glow * 0.5}
          filter={`url(#${uid}-soft)`}
        />
      )}

      <g transform={`rotate(${face.tilt} 60 60) translate(0 ${face.bob * 40})`}>
        {/* ear-pods first, so the shell overlaps their inner edge */}
        {[0, 1].map((side) => {
          const x = side === 0 ? 4 : 106 - POD.w;
          return (
            <g key={`pod-${side}`} data-part="pod">
              {!compact && face.pod > 0.08 && (
                <rect
                  x={x + 2}
                  y={POD.y + 8}
                  width={POD.w - 4}
                  height={POD.h - 16}
                  rx={(POD.w - 4) / 2}
                  fill={accent}
                  opacity={face.pod * 0.7}
                  filter={`url(#${uid}-soft)`}
                />
              )}
              <rect x={x} y={POD.y} width={POD.w} height={POD.h} rx={POD.r} fill="#151a24" stroke="rgba(255,255,255,0.08)" />
              {/* the pod's own light: bright when he hears you or speaks */}
              <rect
                x={x + 4.5}
                y={POD.y + 8}
                width={POD.w - 9}
                height={POD.h - 16}
                rx={(POD.w - 9) / 2}
                fill={accent}
                opacity={0.18 + face.pod * 0.82}
              />
            </g>
          );
        })}

        {/* the shell */}
        <rect
          data-part="shell"
          x={SHELL.x}
          y={SHELL.y}
          width={SHELL.w}
          height={SHELL.h}
          rx={SHELL.r}
          fill={`url(#${uid}-shell)`}
          stroke="rgba(255,255,255,0.10)"
          strokeWidth={1}
        />
        {/* the rim: the same light as the pods, around the whole head */}
        <rect
          data-part="rim"
          x={SHELL.x + 0.9}
          y={SHELL.y + 0.9}
          width={SHELL.w - 1.8}
          height={SHELL.h - 1.8}
          rx={SHELL.r - 0.9}
          fill="none"
          stroke={accent}
          strokeWidth={1.5}
          opacity={0.22 + face.glow * 0.5}
        />
        {!compact && (
          <rect
            x={SHELL.x + 3}
            y={SHELL.y + 3}
            width={SHELL.w - 6}
            height={SHELL.h - 6}
            rx={SHELL.r - 3}
            fill="none"
            stroke="rgba(255,255,255,0.06)"
            strokeWidth={0.8}
          />
        )}

        {/* full-face dark glass: eyes, brows, mouth and bars all sit on this */}
        <rect
          data-part="visor"
          x={VISOR.x}
          y={VISOR.y}
          width={VISOR.w}
          height={VISOR.h}
          rx={VISOR.r}
          fill={`url(#${uid}-visor)`}
        />
        <rect
          x={VISOR.x + 0.6}
          y={VISOR.y + 0.6}
          width={VISOR.w - 1.2}
          height={VISOR.h - 1.2}
          rx={VISOR.r - 0.6}
          fill="none"
          stroke="rgba(255,255,255,0.07)"
          strokeWidth={0.9}
        />

        {/* brows: only when the face has an opinion */}
        {showBrow && (
          <g data-part="brow" stroke={accent} strokeWidth={2} strokeLinecap="round" opacity={0.9}>
            <path d={`M38 ${40.5 + browLift} L52 ${40.5 - browLift}`} />
            <path d={`M82 ${40.5 + browLift} L68 ${40.5 - browLift}`} />
          </g>
        )}

        {EYES.map(({ cx, cy }, i) =>
          face.squint > 0.5 ? (
            // happy eyes are curved shut, like a person smiling with their eyes
            <path
              key={`eye-${cx}`}
              data-part="eye"
              d={`M${cx - 7} ${cy + 1} Q${cx} ${cy - 6.5} ${cx + 7} ${cy + 1}`}
              fill="none"
              stroke={accent}
              strokeWidth={3.4}
              strokeLinecap="round"
            />
          ) : (
            <g key={`eye-${cx}`} data-part="eye">
              <ellipse cx={cx} cy={cy} rx={8} ry={eyeRy} fill={accent} opacity={0.96} />
              {!compact && eyeRy > 3 && (
                <circle cx={cx + pupilX} cy={cy + pupilY} r={3.1} fill="#07090d" opacity={0.9} />
              )}
              {i === 0 && !compact && (
                <ellipse cx={cx - 2.4} cy={cy - eyeRy * 0.35} rx={2} ry={1.6} fill="#ffffff" opacity={0.55} />
              )}
            </g>
          ),
        )}

        {/* the mouth, or the sound itself */}
        {face.bars.length === VOICE_BARS ? (
          <g data-part="bars">
            {face.bars.map((bar, i) => {
              const h = 3 + bar * 13;
              const x = 60 + (i - (VOICE_BARS - 1) / 2) * 8;
              return (
                <g key={`bar-${i}`}>
                  {/* a soft halo so the bars read as light, not as paint */}
                  <rect x={x - 2.8} y={76 - h / 2} width={5.6} height={h} rx={2} fill={accent} opacity={0.3 + bar * 0.3} />
                  {/* and a bright core, which is what actually reads as speech */}
                  <rect
                    x={x - 1.35}
                    y={76 - (h * 0.86) / 2}
                    width={2.7}
                    height={h * 0.86}
                    rx={1.35}
                    fill="#ffffff"
                    opacity={0.72 + bar * 0.28}
                  />
                </g>
              );
            })}
          </g>
        ) : face.dots.length === THINKING_DOTS ? (
          <g data-part="dots">
            {face.dots.map((dot, i) => (
              <circle
                key={`dot-${i}`}
                cx={60 + (i - (THINKING_DOTS - 1) / 2) * 8}
                cy={76}
                r={1.6 + dot * 1.5}
                fill={accent}
                opacity={0.25 + dot * 0.75}
              />
            ))}
          </g>
        ) : (
          <path
            data-part="mouth"
            d={`M${60 - mouthHalf} 78 Q60 ${78 + mouthSag} ${60 + mouthHalf} 78`}
            fill="none"
            stroke={accent}
            strokeWidth={mouthWidth}
            strokeLinecap="round"
            opacity={0.92}
          />
        )}
      </g>

      {/* crown: a small light above the visor, outside the tilt so it reads as a status pip */}
      <g data-part="crown">
        {!compact && (
          <circle cx={60} cy={25} r={4.4} fill={accent} opacity={face.pip * 0.5} filter={`url(#${uid}-soft)`} />
        )}
        <path
          d="M44 26 Q52 23 56.5 25"
          fill="none"
          stroke={accent}
          strokeWidth={1.4}
          strokeLinecap="round"
          opacity={0.28 + face.pip * 0.2}
        />
        <path
          d="M76 26 Q68 23 63.5 25"
          fill="none"
          stroke={accent}
          strokeWidth={1.4}
          strokeLinecap="round"
          opacity={0.28 + face.pip * 0.2}
        />
        <circle cx={60} cy={25} r={2.2} fill={accent} opacity={0.35 + face.pip * 0.65} />
      </g>
    </svg>
  );
}
