import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import JarvisHead from './JarvisHead';
import { FACE_LABELS, FACE_STATES } from './jarvisFace';

const draw = (props: Parameters<typeof JarvisHead>[0]) => render(<JarvisHead {...props} />);

const parts = (container: HTMLElement, part: string): Element[] =>
  Array.from(container.querySelectorAll(`[data-part="${part}"]`));

/** The width of the head shell where it is widest, from the icon's path. */
const SHELL_LEFT = 30;
const SHELL_RIGHT = 78;

describe('JarvisHead', () => {
  it('draws every state and says what it is doing', () => {
    for (const state of FACE_STATES) {
      const { container, unmount } = draw({ state, tickMs: 400 });
      const svg = container.querySelector('svg')!;
      expect(svg.getAttribute('data-face-state')).toBe(state);
      expect(svg.getAttribute('aria-label')).toBe(`Jarvis, ${FACE_LABELS[state]}`);
      expect(svg.getAttribute('viewBox')).toBe('0 0 108 108');
      unmount();
    }
  });

  it('is the launcher mascot, not a helmet', () => {
    const { container } = draw({ state: 'idle', tickMs: 0 });
    const shell = parts(container, 'shell')[0];
    expect(shell).toBeTruthy();
    expect(shell.getAttribute('d')).toMatch(/^M30,54 C30,37 40,27 54,27/);
    // the crest and the visor are the icon's too
    expect(parts(container, 'crest')[0].querySelector('path')!.getAttribute('d')).toMatch(
      /^M58,12 L46,26/,
    );
    expect(parts(container, 'visor')[0].getAttribute('d')).toMatch(/^M39,45 h30/);
  });

  it('gives him two ears, symmetrical about the middle and outside the head', () => {
    const { container } = draw({ state: 'idle', tickMs: 0 });
    const ears = parts(container, 'ear');
    expect(ears).toHaveLength(2);

    const boxes = ears.map((ear) => {
      const rect = ear.querySelector('rect')!;
      const x = Number(rect.getAttribute('x'));
      const width = Number(rect.getAttribute('width'));
      return { x, width, edge: x + width };
    });

    const left = boxes.find((box) => box.edge < 54)!;
    const right = boxes.find((box) => box.x > 54)!;

    // symmetry about the mascot's centre line
    expect(54 - left.x).toBe(right.edge - 54);
    // and both actually stick out past the shell, which is what went wrong
    // in the version that hid one ear behind the head
    expect(left.x).toBeLessThan(SHELL_LEFT);
    expect(right.edge).toBeGreaterThan(SHELL_RIGHT);
    expect(parts(container, 'ear')[0].getAttribute('data-side')).toBe('left');
    expect(parts(container, 'ear')[1].getAttribute('data-side')).toBe('right');
  });

  it('keeps the friendly eyes: a sparkle on both, no pupils, no brows', () => {
    for (const state of FACE_STATES) {
      const { container, unmount } = draw({ state, tickMs: 400 });
      expect(parts(container, 'brow')).toHaveLength(0);
      expect(parts(container, 'pupil')).toHaveLength(0);
      // A happy face swaps in `^ ^` arcs instead of the ovals.
      if (state === 'happy') {
        expect(parts(container, 'eye-smile')).toHaveLength(2);
      } else {
        expect(parts(container, 'eye')).toHaveLength(2);
        if (state !== 'sleeping') expect(parts(container, 'catchlight')).toHaveLength(2);
      }
      unmount();
    }
  });

  it('shuts the eyes when asleep and opens them when listening', () => {
    const asleep = draw({ state: 'sleeping', tickMs: 100 }).container.querySelector('svg')!;
    const awake = draw({ state: 'listening', tickMs: 100 }).container.querySelector('svg')!;
    expect(asleep.getAttribute('data-eye-open')).toBe('0.00');
    expect(Number(awake.getAttribute('data-eye-open'))).toBeGreaterThan(1);
  });

  it('lights both ears with the room', () => {
    const quiet = draw({ state: 'listening', level: 0, tickMs: 400 }).container.querySelector('svg')!;
    const loud = draw({ state: 'listening', level: 95, tickMs: 400 }).container.querySelector('svg')!;
    expect(Number(loud.getAttribute('data-ear'))).toBeGreaterThan(
      Number(quiet.getAttribute('data-ear')),
    );
    expect(Number(loud.getAttribute('data-crest'))).toBeGreaterThan(
      Number(quiet.getAttribute('data-crest')),
    );
  });

  it('speaks inside a dark mouth well, with bars that move', () => {
    const { container } = draw({ state: 'speaking', tickMs: 100 });
    const well = parts(container, 'mouth-well')[0]!;
    expect(well.getAttribute('fill')).toBe('#1e1b4b');
    const box = {
      x: Number(well.getAttribute('x')),
      y: Number(well.getAttribute('y')),
      right: Number(well.getAttribute('x')) + Number(well.getAttribute('width')),
      bottom: Number(well.getAttribute('y')) + Number(well.getAttribute('height')),
    };

    const bars = parts(container, 'bar');
    expect(bars).toHaveLength(5);
    for (const bar of bars) {
      const x = Number(bar.getAttribute('x'));
      const y = Number(bar.getAttribute('y'));
      expect(bar.getAttribute('fill')).toBe('#ffffff');
      expect(x).toBeGreaterThanOrEqual(box.x);
      expect(x + Number(bar.getAttribute('width'))).toBeLessThanOrEqual(box.right);
      expect(y).toBeGreaterThanOrEqual(box.y);
      expect(y + Number(bar.getAttribute('height'))).toBeLessThanOrEqual(box.bottom);
    }

    const later = draw({ state: 'speaking', tickMs: 260 });
    const heights = (list: Element[]) =>
      list.map((bar) => Number(bar.getAttribute('height')));
    expect(heights(bars)).not.toEqual(heights(parts(later.container, 'bar')));
  });

  it('shows three dots and no mouth while thinking', () => {
    const { container } = draw({ state: 'thinking', tickMs: 700 });
    expect(parts(container, 'dot')).toHaveLength(3);
    expect(parts(container, 'mouth')).toHaveLength(0);
    expect(parts(container, 'bar')).toHaveLength(0);
  });

  it('turns amber and tips his head when something goes wrong', () => {
    const { container } = draw({ state: 'error', tickMs: 0 });
    const crest = parts(container, 'crest')[0].querySelector('path')!;
    expect(crest.getAttribute('fill')).toBe('var(--jarvis-warm, #fbbf24)');
    const group = container.querySelector('g[transform]')!;
    expect(group.getAttribute('transform')).toContain('rotate(-5.00');
    expect(parts(container, 'brow')).toHaveLength(0);
  });

  it('keeps gradient ids unique so two heads never collide', () => {
    const first = draw({ state: 'idle', tickMs: 0 }).container.innerHTML;
    const second = draw({ state: 'idle', tickMs: 0 }).container.innerHTML;
    const ids = (html: string) => Array.from(html.matchAll(/id="([^"]+)"/g)).map((m) => m[1]);
    expect(ids(first)).toHaveLength(3);
    expect(ids(first).some((id) => ids(second).includes(id))).toBe(false);
  });

  it('stays legible at 24-32px in compact mode', () => {
    const { container } = draw({ state: 'speaking', size: 32, compact: true, tickMs: 100 });
    expect(parts(container, 'glow')).toHaveLength(0);
    expect(container.querySelectorAll('[filter]')).toHaveLength(0);
    expect(parts(container, 'ear')).toHaveLength(2);
    expect(parts(container, 'eye')).toHaveLength(2);
    expect(parts(container, 'bar')).toHaveLength(5);
  });
});
