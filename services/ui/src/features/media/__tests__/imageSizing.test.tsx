import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';

// A held media token, so `proxiedImage` builds a real proxy URL to inspect.
vi.mock('../../../lib/mediaToken', () => {
  const token = { token: 'mt-1', user: 'alice', expiresAt: Math.floor(Date.now() / 1000) + 3600 };
  return {
    useMediaToken: () => token,
    currentMediaToken: () => token,
    ensureMediaToken: () => Promise.resolve(token),
  };
});

import { Artwork } from '../components/Artwork';

const originalDpr = window.devicePixelRatio;

afterEach(() => {
  Object.defineProperty(window, 'devicePixelRatio', { value: originalDpr, configurable: true });
});

describe('P7-T5 image sizing', () => {
  it('draws artwork at its rendered size, lazily, off the main thread', () => {
    const { container } = render(<Artwork image="/cover.jpg" kind="track" size={44} />);
    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img?.getAttribute('loading')).toBe('lazy');
    expect(img?.getAttribute('decoding')).toBe('async');
    expect(img?.getAttribute('width')).toBe('44');
    expect(img?.getAttribute('height')).toBe('44');
    expect(img?.getAttribute('sizes')).toBe('44px');
    // A 44 px thumbnail asks the proxy for 44 px, not a full-size cover.
    expect(img?.getAttribute('src')).toContain('w=44');
  });

  it('offers a 2x candidate on a high-density screen instead of downloading one', () => {
    Object.defineProperty(window, 'devicePixelRatio', { value: 2, configurable: true });
    const { container } = render(<Artwork image="/cover.jpg" kind="track" size={44} />);
    const img = container.querySelector('img');
    expect(img?.getAttribute('src')).toContain('w=88');
    const srcSet = img?.getAttribute('srcSet') ?? '';
    expect(srcSet).toContain('44w');
    expect(srcSet).toContain('88w');
  });

  it('shows the icon placeholder, with no image, when there is no art', () => {
    const { container } = render(<Artwork image={null} kind="album" size={64} />);
    expect(container.querySelector('img')).toBeNull();
  });
});
