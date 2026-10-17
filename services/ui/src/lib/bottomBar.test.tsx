import { describe, it, expect, vi, afterEach } from 'vitest';
import { render } from '@testing-library/react';
import { useRef } from 'react';
import { usePublishBottomBarHeight } from './bottomBar';

function Bar({ height }: { height: number }) {
  const ref = useRef<HTMLElement>(null);
  usePublishBottomBarHeight(ref);
  return <nav ref={ref} data-height={height} />;
}

describe('usePublishBottomBarHeight', () => {
  afterEach(() => vi.restoreAllMocks());

  it('publishes the bar height while it is shown and clears it after', () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ height: 88.4 } as DOMRect);
    const { unmount } = render(<Bar height={88} />);
    // Rounded up: a fraction of a pixel short would still hide a line's descenders.
    expect(document.documentElement.style.getPropertyValue('--bottom-bar')).toBe('89px');
    unmount();
    expect(document.documentElement.style.getPropertyValue('--bottom-bar')).toBe('');
  });
});
