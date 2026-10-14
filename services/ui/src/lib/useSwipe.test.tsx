import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { useSwipe } from './useSwipe';

function Pad({ left, right }: { left: () => void; right: () => void }) {
  return <div data-testid="pad" {...useSwipe(left, right)} />;
}

const swipe = (el: HTMLElement, dx: number, dy = 0) => {
  fireEvent.touchStart(el, { touches: [{ clientX: 200, clientY: 200 }] });
  fireEvent.touchEnd(el, { changedTouches: [{ clientX: 200 + dx, clientY: 200 + dy }] });
};

describe('swipe to turn the page', () => {
  it('turns left and right on a sideways swipe', () => {
    const left = vi.fn();
    const right = vi.fn();
    render(<Pad left={left} right={right} />);
    swipe(screen.getByTestId('pad'), -120);
    swipe(screen.getByTestId('pad'), 120);
    expect(left).toHaveBeenCalledTimes(1);
    expect(right).toHaveBeenCalledTimes(1);
  });

  it('ignores a scroll that drifts sideways', () => {
    const left = vi.fn();
    render(<Pad left={left} right={vi.fn()} />);
    swipe(screen.getByTestId('pad'), -80, 200);
    swipe(screen.getByTestId('pad'), -30);
    expect(left).not.toHaveBeenCalled();
  });
});
