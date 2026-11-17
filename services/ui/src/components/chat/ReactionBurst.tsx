import { useEffect, useRef } from 'react';
import { prefersReducedMotion, reactionKind, type ReactionKind } from './privateChatModel';

/**
 * The animation a reaction plays over its bubble, one per emoji: hearts float
 * up and burst, a thumbs up pops and bounces, a laugh shakes with sparkles, a
 * wow zooms out of a ring, a sad face droops with a falling tear, praying hands
 * rise into a soft glow. Web Animations only (no library); with
 * prefers-reduced-motion the emoji just fades in and out.
 */

interface Part {
  /** What is drawn: an emoji, or a shape drawn by its class. */
  text?: string;
  className: string;
  size: number;
  keyframes: Keyframe[];
  options: KeyframeAnimationOptions;
}

const EASE_OUT = 'cubic-bezier(0.22, 1, 0.36, 1)';
const DURATION = 1500;

/** Points spread around a circle, so the parts never need randomness to look scattered. */
function around(count: number, radius: number, start = -90): Array<{ x: number; y: number; angle: number }> {
  return Array.from({ length: count }, (_, i) => {
    const angle = start + (360 / count) * i;
    const rad = (angle * Math.PI) / 180;
    return { x: Math.round(Math.cos(rad) * radius), y: Math.round(Math.sin(rad) * radius), angle };
  });
}

function main(emoji: string, keyframes: Keyframe[], size = 56): Part {
  return { text: emoji, className: 'drop-shadow-[0_4px_12px_rgba(0,0,0,0.45)]', size, keyframes, options: { duration: DURATION, easing: 'ease-out', fill: 'forwards' } };
}

function parts(kind: ReactionKind, emoji: string): Part[] {
  switch (kind) {
    case 'love': {
      const drift = [-58, 44, -26, 66, -70, 18, 52, -40];
      const hearts = drift.map((x, i): Part => ({
        text: '❤️',
        className: '',
        size: 16 + (i % 3) * 6,
        keyframes: [
          { transform: 'translate(0, 12px) scale(0.3)', opacity: 0 },
          { transform: `translate(${x * 0.4}px, -24px) scale(1)`, opacity: 1, offset: 0.3 },
          { transform: `translate(${x}px, ${-78 - (i % 4) * 10}px) scale(0.8)`, opacity: 0 },
        ],
        options: { duration: 1100, delay: 120 + i * 70, easing: EASE_OUT, fill: 'both' },
      }));
      return [
        { className: 'rounded-full bg-rose-500/40 blur-xl', size: 96, keyframes: [
          { transform: 'scale(0.2)', opacity: 0 }, { transform: 'scale(1)', opacity: 0.9, offset: 0.35 }, { transform: 'scale(1.6)', opacity: 0 },
        ], options: { duration: 1100, easing: EASE_OUT, fill: 'forwards' } },
        main(emoji, [
          { transform: 'scale(0.2)', opacity: 0 },
          { transform: 'scale(1.35)', opacity: 1, offset: 0.25 },
          { transform: 'scale(1.1)', opacity: 1, offset: 0.4 },
          { transform: 'scale(1.3)', opacity: 1, offset: 0.55 },
          { transform: 'scale(2.2)', opacity: 0 },
        ]),
        ...hearts,
      ];
    }
    case 'like':
      return [
        ...around(8, 58).map(({ x, y }, i): Part => ({
          className: 'rounded-full bg-sky-300',
          size: 7,
          keyframes: [
            { transform: 'translate(0, 0) scale(0)', opacity: 0 },
            { transform: `translate(${x * 0.5}px, ${y * 0.5}px) scale(1)`, opacity: 1, offset: 0.4 },
            { transform: `translate(${x}px, ${y}px) scale(0.2)`, opacity: 0 },
          ],
          options: { duration: 700, delay: 260 + (i % 2) * 40, easing: EASE_OUT, fill: 'both' },
        })),
        main(emoji, [
          { transform: 'translateY(24px) scale(0) rotate(-20deg)', opacity: 0 },
          { transform: 'translateY(-8px) scale(1.45) rotate(8deg)', opacity: 1, offset: 0.22 },
          { transform: 'translateY(0) scale(1) rotate(0deg)', opacity: 1, offset: 0.36 },
          { transform: 'translateY(-16px) scale(1.08)', opacity: 1, offset: 0.5 },
          { transform: 'translateY(0) scale(1)', opacity: 1, offset: 0.62 },
          { transform: 'translateY(-6px) scale(1.03)', opacity: 1, offset: 0.72 },
          { transform: 'translateY(0) scale(1)', opacity: 1, offset: 0.82 },
          { transform: 'translateY(0) scale(0.9)', opacity: 0 },
        ]),
      ];
    case 'laugh':
      return [
        ...around(6, 62, -60).map(({ x, y, angle }, i): Part => ({
          text: '✨',
          className: '',
          size: 18,
          keyframes: [
            { transform: `translate(0, 0) scale(0) rotate(${angle}deg)`, opacity: 0 },
            { transform: `translate(${x * 0.6}px, ${y * 0.6}px) scale(1.1) rotate(${angle + 90}deg)`, opacity: 1, offset: 0.45 },
            { transform: `translate(${x}px, ${y}px) scale(0.4) rotate(${angle + 180}deg)`, opacity: 0 },
          ],
          options: { duration: 900, delay: 150 + i * 90, easing: EASE_OUT, fill: 'both' },
        })),
        main(emoji, [
          { transform: 'scale(0.3) rotate(0deg)', opacity: 0 },
          { transform: 'scale(1.3) rotate(-14deg)', opacity: 1, offset: 0.15 },
          { transform: 'scale(1.3) rotate(14deg)', opacity: 1, offset: 0.25 },
          { transform: 'scale(1.3) rotate(-12deg)', opacity: 1, offset: 0.35 },
          { transform: 'scale(1.3) rotate(12deg)', opacity: 1, offset: 0.45 },
          { transform: 'scale(1.25) rotate(-8deg)', opacity: 1, offset: 0.55 },
          { transform: 'scale(1.25) rotate(8deg)', opacity: 1, offset: 0.65 },
          { transform: 'scale(1.2) rotate(0deg)', opacity: 1, offset: 0.78 },
          { transform: 'scale(1.4) rotate(0deg)', opacity: 0 },
        ]),
      ];
    case 'wow':
      return [
        ...[0, 260].map((delay): Part => ({
          className: 'rounded-full border-[3px] border-amber-200/90',
          size: 70,
          keyframes: [{ transform: 'scale(0.3)', opacity: 0.95 }, { transform: 'scale(2.3)', opacity: 0 }],
          options: { duration: 900, delay: 120 + delay, easing: EASE_OUT, fill: 'both' },
        })),
        main(emoji, [
          { transform: 'scale(0)', opacity: 0 },
          { transform: 'scale(1.7)', opacity: 1, offset: 0.2 },
          { transform: 'scale(1.35)', opacity: 1, offset: 0.32 },
          { transform: 'scale(1.45)', opacity: 1, offset: 0.7 },
          { transform: 'scale(1.9)', opacity: 0 },
        ]),
      ];
    case 'sad':
      return [
        { className: 'rounded-full bg-sky-400/30 blur-xl', size: 90, keyframes: [
          { opacity: 0 }, { opacity: 0.8, offset: 0.4 }, { opacity: 0 },
        ], options: { duration: DURATION, easing: 'ease-in-out', fill: 'forwards' } },
        main(emoji, [
          { transform: 'translateY(-14px) scale(0.7)', opacity: 0 },
          { transform: 'translateY(0) scale(1.25) rotate(-4deg)', opacity: 1, offset: 0.3 },
          { transform: 'translateY(6px) scale(1.2) rotate(-7deg)', opacity: 1, offset: 0.7 },
          { transform: 'translateY(18px) scale(1.1) rotate(-8deg)', opacity: 0 },
        ]),
        { text: '💧', className: '', size: 16, keyframes: [
          { transform: 'translate(10px, 6px) scale(0.4)', opacity: 0 },
          { transform: 'translate(10px, 14px) scale(1)', opacity: 1, offset: 0.25 },
          { transform: 'translate(12px, 70px) scale(0.8)', opacity: 0 },
        ], options: { duration: 1000, delay: 420, easing: 'cubic-bezier(0.55, 0, 1, 0.45)', fill: 'both' } },
      ];
    case 'pray':
      return [
        { className: 'rounded-full bg-[radial-gradient(circle,rgba(253,230,138,0.85)_0%,rgba(251,191,36,0.35)_45%,transparent_70%)]', size: 120, keyframes: [
          { transform: 'scale(0.4)', opacity: 0 },
          { transform: 'scale(1)', opacity: 1, offset: 0.35 },
          { transform: 'scale(0.85)', opacity: 0.7, offset: 0.55 },
          { transform: 'scale(1.1)', opacity: 0.9, offset: 0.75 },
          { transform: 'scale(1.5)', opacity: 0 },
        ], options: { duration: DURATION, easing: 'ease-in-out', fill: 'forwards' } },
        ...[-34, -12, 12, 34].map((x, i): Part => ({
          className: 'rounded-full bg-amber-100',
          size: 5,
          keyframes: [
            { transform: `translate(${x}px, 10px)`, opacity: 0 },
            { transform: `translate(${x}px, -20px)`, opacity: 1, offset: 0.4 },
            { transform: `translate(${x * 1.2}px, -70px)`, opacity: 0 },
          ],
          options: { duration: 1000, delay: 300 + i * 110, easing: 'ease-out', fill: 'both' },
        })),
        main(emoji, [
          { transform: 'translateY(22px) scale(0.6)', opacity: 0 },
          { transform: 'translateY(-4px) scale(1.3)', opacity: 1, offset: 0.35 },
          { transform: 'translateY(-8px) scale(1.35)', opacity: 1, offset: 0.75 },
          { transform: 'translateY(-14px) scale(1.4)', opacity: 0 },
        ]),
      ];
  }
}

function reduced(emoji: string): Part[] {
  return [main(emoji, [{ opacity: 0 }, { opacity: 1, offset: 0.3 }, { opacity: 1, offset: 0.7 }, { opacity: 0 }], 48)];
}

export default function ReactionBurst({ emoji, onDone }: { emoji: string; onDone: () => void }) {
  const kind = reactionKind(emoji) ?? 'love';
  const calm = prefersReducedMotion();
  const list = calm ? reduced(emoji) : parts(kind, emoji);
  const refs = useRef<Array<HTMLSpanElement | null>>([]);
  const doneRef = useRef(onDone);
  useEffect(() => {
    doneRef.current = onDone;
  }, [onDone]);

  // Once per burst (the parent keys it by reaction): run every part, then report done.
  useEffect(() => {
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      doneRef.current();
    };
    const animations: Animation[] = [];
    list.forEach((part, i) => {
      const el = refs.current[i];
      if (el && typeof el.animate === 'function') animations.push(el.animate(part.keyframes, part.options));
    });
    // The longest part sets the end; a browser without Web Animations still ends on time.
    const longest = Math.max(...list.map((p) => Number(p.options.duration ?? 0) + Number(p.options.delay ?? 0)));
    const timer = window.setTimeout(finish, longest + 80);
    return () => {
      window.clearTimeout(timer);
      for (const animation of animations) animation.cancel();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one run per mounted burst
  }, []);

  return (
    <span
      aria-hidden
      data-testid="reaction-animation"
      data-kind={calm ? 'calm' : kind}
      className="pointer-events-none absolute left-1/2 top-1/2 z-20 h-0 w-0"
    >
      {list.map((part, i) => (
        <span
          key={i}
          ref={(el) => {
            refs.current[i] = el;
          }}
          className={`absolute flex select-none items-center justify-center leading-none opacity-0 ${part.className}`}
          style={{ width: part.size, height: part.size, left: -part.size / 2, top: -part.size / 2, fontSize: part.text ? part.size * 0.9 : undefined }}
        >
          {part.text}
        </span>
      ))}
    </span>
  );
}
