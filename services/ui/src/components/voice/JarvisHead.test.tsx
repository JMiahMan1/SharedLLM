import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import JarvisHead from './JarvisHead';
import { FACE_STATES, THINKING_DOTS } from './jarvisFace';

describe('JarvisHead', () => {
  it('renders every state with its own look', () => {
    for (const state of FACE_STATES) {
      const { container, unmount } = render(<JarvisHead state={state} />);
      const svg = container.querySelector('svg');
      expect(svg?.getAttribute('data-face-state')).toBe(state);
      expect(svg?.getAttribute('aria-label')).toBeTruthy();
      unmount();
    }
  });

  it('is a rounded companion head, not the pointy shield', () => {
    const { container } = render(<JarvisHead state="idle" />);
    const shell = container.querySelector('rect[data-part="shell"]');
    expect(shell).not.toBeNull();
    // The v1 heraldic shield started with this exact path; it must be gone.
    expect(container.querySelector('path[d^="M60 6 L104 21"]')).toBeNull();
    // A squircle: the corner radius is a big fraction of the shorter side, so it
    // reads as a head rather than as a badge.
    const width = Number(shell?.getAttribute('width'));
    const height = Number(shell?.getAttribute('height'));
    const radius = Number(shell?.getAttribute('rx'));
    expect(radius).toBeGreaterThan(Math.min(width, height) * 0.3);
  });

  it('keeps every facial light inside the dark visor', () => {
    // This is the bug that shipped in v1: the mouth and the voice bars were
    // drawn in the accent colour on top of the accent-coloured shield, so a
    // speaking face looked identical to a silent one. Everything luminous must
    // be enclosed by the dark glass.
    const { container } = render(<JarvisHead state="speaking" />);
    const visor = container.querySelector('rect[data-part="visor"]');
    const top = Number(visor?.getAttribute('y'));
    const bottom = top + Number(visor?.getAttribute('height'));
    const left = Number(visor?.getAttribute('x'));
    const right = left + Number(visor?.getAttribute('width'));

    for (const eye of container.querySelectorAll('[data-part="eye"] ellipse')) {
      const cy = Number(eye.getAttribute('cy'));
      const ry = Number(eye.getAttribute('ry'));
      expect(cy - ry).toBeGreaterThan(top);
      expect(cy + ry).toBeLessThan(bottom);
    }
    for (const bar of container.querySelectorAll('[data-part="bars"] rect')) {
      const y = Number(bar.getAttribute('y'));
      const h = Number(bar.getAttribute('height'));
      const x = Number(bar.getAttribute('x'));
      const w = Number(bar.getAttribute('width'));
      expect(y).toBeGreaterThan(top);
      expect(y + h).toBeLessThan(bottom);
      expect(x).toBeGreaterThan(left);
      expect(x + w).toBeLessThan(right);
    }
  });

  it('draws the mouth inside the visor when it is not speaking', () => {
    const { container } = render(<JarvisHead state="idle" />);
    const visor = container.querySelector('rect[data-part="visor"]')!;
    const bottom = Number(visor.getAttribute('y')) + Number(visor.getAttribute('height'));
    expect(container.querySelector('[data-part="mouth"]')).not.toBeNull();
    // The mouth curve sits at y=78 and sags by at most 5.5.
    expect(bottom).toBeGreaterThan(84);
  });

  it('closes his eyes when he is resting and opens them when he listens', () => {
    const asleep = render(<JarvisHead state="sleeping" />);
    expect(asleep.container.querySelector('svg')?.getAttribute('data-eye-open')).toBe('0.00');
    asleep.unmount();

    const awake = render(<JarvisHead state="listening" />);
    expect(awake.container.querySelector('svg')?.getAttribute('data-eye-open')).toBe('1.00');
  });

  it('lights its ear-pods from the microphone level', () => {
    const quiet = render(<JarvisHead state="listening" level={0} />);
    const loud = render(<JarvisHead state="listening" level={100} />);
    const read = (c: HTMLElement) => Number(c.querySelector('svg')?.getAttribute('data-pod'));
    expect(read(loud.container)).toBeGreaterThan(read(quiet.container));
    expect(loud.container.querySelectorAll('[data-part="pod"]')).toHaveLength(2);
  });

  it('shows five bright voice bars while speaking and a mouth otherwise', () => {
    const talking = render(<JarvisHead state="speaking" />);
    expect(talking.container.querySelectorAll('[data-part="bars"] rect[rx="2"]')).toHaveLength(5);
    // Each bar also gets a white core, which is what reads as speech.
    expect(talking.container.querySelectorAll('[data-part="bars"] rect[fill="#ffffff"]')).toHaveLength(5);
    talking.unmount();

    const quiet = render(<JarvisHead state="idle" />);
    expect(quiet.container.querySelectorAll('[data-part="bars"] rect[rx="2"]')).toHaveLength(0);
  });

  it('shows three thinking dots instead of a mouth while thinking', () => {
    const { container } = render(<JarvisHead state="thinking" />);
    expect(container.querySelectorAll('[data-part="dots"] circle')).toHaveLength(THINKING_DOTS);
    expect(container.querySelector('[data-part="mouth"]')).toBeNull();
  });

  it('lifts its brows when something is wrong and lowers them never', () => {
    const worried = render(<JarvisHead state="error" />);
    expect(worried.container.querySelector('[data-part="brow"]')).not.toBeNull();
    worried.unmount();

    const calm = render(<JarvisHead state="idle" />);
    expect(calm.container.querySelector('[data-part="brow"]')).toBeNull();
  });

  it('gives each instance its own gradient ids so two heads do not collide', () => {
    const a = render(<JarvisHead state="idle" />);
    const b = render(<JarvisHead state="idle" />);
    const first = a.container.querySelector('linearGradient')?.getAttribute('id');
    const second = b.container.querySelector('linearGradient')?.getAttribute('id');
    expect(first).toBeTruthy();
    expect(second).toBeTruthy();
    expect(first).not.toBe(second);
  });

  it('keeps its details out of the way at icon size', () => {
    const { container } = render(<JarvisHead state="idle" size={32} compact />);
    // No blur filters (expensive and muddy at 24 px) and no pupils.
    expect(container.querySelector('[filter]')).toBeNull();
    expect(container.querySelectorAll('[data-part="eye"] circle')).toHaveLength(0);
    // It is still recognisably Jarvis: shell, visor, eyes.
    expect(container.querySelector('rect[data-part="shell"]')).not.toBeNull();
    expect(container.querySelector('rect[data-part="visor"]')).not.toBeNull();
    expect(container.querySelectorAll('[data-part="eye"]')).toHaveLength(2);
  });

  it('still shows the speaking bars at icon size', () => {
    const { container } = render(<JarvisHead state="speaking" size={32} compact />);
    expect(container.querySelectorAll('[data-part="bars"] rect[rx="2"]')).toHaveLength(5);
  });
});
