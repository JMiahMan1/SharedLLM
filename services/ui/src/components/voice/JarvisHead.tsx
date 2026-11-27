import { useEffect, useId, useRef, useState } from 'react';
import { useReducedMotion } from 'framer-motion';
import { faceFor, type FaceState } from './jarvisFace';

/**
 * Jarvis's head, drawn as the app's own mascot.
 *
 * The geometry is not invented here: every path below is the one in
 * `services/ui/android/app/src/main/res/drawable/ic_launcher_foreground.xml`,
 * the mark on the launcher icon, scaled into the same 108-unit grid. The two
 * side accents, the lightning-bolt crest, the deep-indigo visor, the sparkling
 * eyes and the rosy cheeks are that drawing; this component only moves them.
 *
 * The drawing is deliberately friendly: there are no brows and no black
 * pupils in it, in any state. An earlier version of this file replaced the
 * mascot with a dark angular helmet - it read as mean on a real phone and it
 * lost the ears behind the shell - so the shapes here are pinned by tests.
 */

const VIEW = 108;

/** The head shell, exactly as the launcher icon draws it. */
const SHELL =
  'M30,54 C30,37 40,27 54,27 C68,27 78,37 78,54 L78,65 C78,74 71,80 62,80 L46,80 C37,80 30,74 30,65 Z';

/** The soft light across the top of the shell. */
const HIGHLIGHT = 'M40,40 C45,33 63,33 68,40 C60,36 48,36 40,40 Z';

/** The visor: one continuous dark band over both eyes. */
const VISOR =
  'M39,45 h30 a7,7 0 0,1 7,7 v6 a7,7 0 0,1 -7,7 h-30 a7,7 0 0,1 -7,-7 v-6 a7,7 0 0,1 7,-7 Z';

/** The signature crest above the head. */
const CREST = 'M58,12 L46,26 H52.5 L49,38 L63,22 H56.5 L60,12 Z';

/** The two side accents. Symmetrical about x = 54, and outside the shell. */
const EAR = { y: 48, width: 9, height: 15, radius: 3 } as const;
const EAR_LEFT_X = 25;
const EAR_RIGHT_X = 74;

/** Eye centres, from the launcher icon. */
const EYES = [
  { cx: 46, cy: 55 },
  { cx: 62, cy: 55 },
] as const;
const EYE_RX = 4.2;
const EYE_RY = 5.4;

/** Catchlights, on both eyes - the mascot looks awake because of these. */
const CATCHLIGHT_DX = 1.4;
const CATCHLIGHT_DY = -2.4;

/** Cheeks and the mouth's home, from the launcher icon. */
const CHEEKS = [
  { cx: 39, cy: 64 },
  { cx: 69, cy: 64 },
] as const;
const MOUTH_Y = 70;

export interface JarvisHeadProps {
  state: FaceState;
  /** Microphone level, 0..100. */
  level?: number;
  /** Rendered size in pixels. */
  size?: number;
  /** Drop the glow, halos and other detail that cannot be seen when small. */
  compact?: boolean;
  /**
   * Freeze the animation at this tick. Mostly for tests and for still images;
   * when it is set the component stops animating.
   */
  tickMs?: number;
  className?: string;
}

export default function JarvisHead({
  state,
  level = 0,
  size = 176,
  compact = false,
  tickMs,
  className,
}: JarvisHeadProps) {
  const reduced = useReducedMotion();
  const [tick, setTick] = useState(0);
  const last = useRef(0);
  const frozen = tickMs !== undefined;

  useEffect(() => {
    if (frozen) return;
    // A face only needs ~30 fps: anything faster burns a phone's battery for
    // motion nobody can see.
    let frame = 0;
    const step = (now: number) => {
      if (now - last.current >= 33) {
        last.current = now;
        setTick(now);
      }
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [frozen]);

  const face = faceFor(state, level, reduced ? 0 : frozen ? tickMs : tick);
  const raw = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const shellId = `jarvis-shell-${raw}`;
  const glowId = `jarvis-glow-${raw}`;
  const softId = `jarvis-soft-${raw}`;

  const amber = face.accent === 'amber';
  const accent = amber ? 'var(--jarvis-warm, #fbbf24)' : 'var(--site-accent, #7c3aed)';
  const accentLight = amber
    ? 'var(--jarvis-warm-light, #fde68a)'
    : 'var(--site-accent-light, #a78bfa)';
  const cheek = `rgba(240, 171, 252, ${(0.12 + face.cheek * 0.45).toFixed(3)})`;
  const crest = amber ? 'var(--jarvis-warm, #fbbf24)' : '#c4b5fd';
  const mouthStroke = amber ? '#fef3c7' : '#ddd6fe';
  const speaking = face.bars.length > 0;
  const thinking = face.dots.length > 0;

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${VIEW} ${VIEW}`}
      className={className}
      role="img"
      aria-label={`Jarvis, ${face.label}`}
      data-face-state={face.state}
      data-eye-open={face.eyeOpen.toFixed(2)}
      data-ear={face.ear.toFixed(2)}
      data-crest={face.crest.toFixed(2)}
    >
      <defs>
        <linearGradient id={shellId} x1="32" y1="28" x2="78" y2="80" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor={accentLight} />
          <stop offset="100%" stopColor={accent} />
        </linearGradient>
        <radialGradient id={glowId}>
          <stop offset="0%" stopColor={accent} stopOpacity="0.55" />
          <stop offset="100%" stopColor={accent} stopOpacity="0" />
        </radialGradient>
        <filter id={softId} x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="2.6" />
        </filter>
      </defs>

      {!compact && (
        <circle
          data-part="glow"
          cx={VIEW / 2}
          cy="54"
          r="40"
          fill={`url(#${glowId})`}
          opacity={face.glow}
        />
      )}

      <g transform={`rotate(${face.tilt.toFixed(2)} 54 54) translate(0 ${(face.bob * 108).toFixed(2)})`}>
        {/* Side accents. Both ears, always, symmetrical about x = 54 - a test
            asserts it so one can never disappear behind the shell again. */}
        {[EAR_LEFT_X, EAR_RIGHT_X].map((x) => (
          <g key={`ear-${x}`} data-part="ear" data-side={x < VIEW / 2 ? 'left' : 'right'}>
            <rect
              x={x}
              y={EAR.y}
              width={EAR.width}
              height={EAR.height}
              rx={EAR.radius}
              fill={accent}
              opacity={0.35 + face.ear * 0.65}
            />
            {!compact && face.ear > 0.35 && (
              <rect
                x={x}
                y={EAR.y}
                width={EAR.width}
                height={EAR.height}
                rx={EAR.radius}
                fill={accent}
                opacity={0.35}
                filter={`url(#${softId})`}
              />
            )}
          </g>
        ))}

        <path d={SHELL} fill={`url(#${shellId})`} data-part="shell" />
        <path d={HIGHLIGHT} fill="#ede9fe" fillOpacity="0.55" data-part="highlight" />
        <path d={VISOR} fill="#1e1b4b" fillOpacity="0.92" data-part="visor" />

        {/* Cheeks sit outside the visor, exactly where the icon puts them. */}
        {CHEEKS.map(({ cx, cy }) => (
          <ellipse
            key={`cheek-${cx}`}
            data-part="cheek"
            cx={cx}
            cy={cy}
            rx="3"
            ry="2"
            fill={cheek}
          />
        ))}

        {/* Eyes: the mascot's own ovals and sparkles. Happy swaps them for
            `^ ^` arcs; nothing else narrows, tilts or darkens them. */}
        {face.squint > 0.5
          ? EYES.map(({ cx, cy }) => (
              <path
                key={`eye-smile-${cx}`}
                data-part="eye-smile"
                d={`M${cx - EYE_RX},${cy + 1} Q${cx},${cy - 3.4} ${cx + EYE_RX},${cy + 1}`}
                fill="none"
                stroke="#ede9fe"
                strokeWidth="2.1"
                strokeLinecap="round"
              />
            ))
          : EYES.map(({ cx, cy }) => {
              const ry = Math.max(0.4, EYE_RY * face.eyeOpen);
              // The mascot has no pupils, so a glance is the eye itself moving
              // a little. It is what makes `thinking` read as looking away.
              const glx = cx + face.lookX * 0.9;
              const dy = face.lookY * 1.2;
              return (
                <g key={`eye-${cx}`}>
                  <ellipse
                    data-part="eye"
                    cx={glx}
                    cy={cy}
                    rx={EYE_RX}
                    ry={ry}
                    fill="#ede9fe"
                  />
                  {!compact && (
                    <circle
                      data-part="catchlight"
                      cx={glx + CATCHLIGHT_DX}
                      cy={cy + CATCHLIGHT_DY + dy}
                      r={1.5 * Math.max(0.55, face.eyeOpen)}
                      fill="#ffffff"
                      fillOpacity="0.92"
                    />
                  )}
                </g>
              );
            })}

        {/* Mouth. Speaking gets its own dark well so the bars always have
            contrast against the purple head. */}
        {speaking ? (
          <g>
            <rect
              data-part="mouth-well"
              x="44"
              y="66.5"
              width="20"
              height="11"
              rx="5.5"
              fill="#1e1b4b"
              fillOpacity="0.95"
            />
            {face.bars.map((bar, index) => {
              const height = 2 + bar * 6;
              const cx = 54 + (index - (face.bars.length - 1) / 2) * 3.4;
              return (
                <rect
                  key={`bar-${index}`}
                  data-part="bar"
                  x={cx - 1}
                  y={72 - height / 2}
                  width="2"
                  height={height}
                  rx="1"
                  fill="#ffffff"
                  fillOpacity={0.82 + bar * 0.18}
                />
              );
            })}
          </g>
        ) : thinking ? (
          face.dots.map((dot, index) => (
            <circle
              key={`dot-${index}`}
              data-part="dot"
              cx={54 + (index - (face.dots.length - 1) / 2) * 5}
              cy="72"
              r={1.2 + dot * 1.1}
              fill={mouthStroke}
              fillOpacity={0.55 + dot * 0.45}
            />
          ))
        ) : (
          <path
            data-part="mouth"
            d={`M48,${MOUTH_Y} Q54,${(MOUTH_Y + face.mouthCurve * 4.5).toFixed(2)} 60,${MOUTH_Y}`}
            fill="none"
            stroke={mouthStroke}
            strokeWidth={(2.4 + face.mouthOpen * 1.8).toFixed(2)}
            strokeLinecap="round"
          />
        )}

        {/* The crest. Amber when something went wrong, otherwise the icon's
            lavender, brightening with attention. */}
        <g data-part="crest" opacity={0.3 + face.crest * 0.7}>
          {!compact && face.crest > 0.55 && (
            <path d={CREST} fill={crest} opacity="0.5" filter={`url(#${softId})`} />
          )}
          <path d={CREST} fill={crest} />
          {!compact && (
            <path d={CREST} fill="none" stroke="#f5f3ff" strokeWidth="1" strokeOpacity="0.65" />
          )}
        </g>
      </g>
    </svg>
  );
}
