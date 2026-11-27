import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import JarvisHead from './JarvisHead';
import { FACE_STATES } from './jarvisFace';

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

  it('draws the shield that the app icon uses, so he is the same character', () => {
    const { container } = render(<JarvisHead state="idle" />);
    const outline = container.querySelector('path[d^="M60 6 L104 21"]');
    expect(outline).not.toBeNull();
  });

  it('closes his eyes when he is resting and opens them when he listens', () => {
    const asleep = render(<JarvisHead state="sleeping" />);
    expect(asleep.container.querySelector('svg')?.getAttribute('data-eye-open')).toBe('0.00');
    asleep.unmount();

    const awake = render(<JarvisHead state="listening" />);
    expect(awake.container.querySelector('svg')?.getAttribute('data-eye-open')).toBe('1.00');
  });

  it('shows five voice bars while speaking and a mouth otherwise', () => {
    const talking = render(<JarvisHead state="speaking" />);
    expect(talking.container.querySelectorAll('rect[rx="2"]')).toHaveLength(5);
    talking.unmount();

    const quiet = render(<JarvisHead state="idle" />);
    expect(quiet.container.querySelectorAll('rect[rx="2"]')).toHaveLength(0);
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
    expect(container.querySelectorAll('circle')).toHaveLength(0);
    expect(container.querySelector('[filter]')).toBeNull();
  });
});
