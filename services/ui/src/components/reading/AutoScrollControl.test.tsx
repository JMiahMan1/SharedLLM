import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import AutoScrollControl from './AutoScrollControl';
import { DEFAULT_SPEED, RESUME_AFTER_MS, SPEEDS, advance, atBottom, clampSpeed, readSpeed } from '../../lib/autoScroll';

vi.mock('../../lib/keepAwake', () => ({ holdScreenOn: vi.fn(() => Promise.resolve(null)) }));

/** A page that scrolls like a browser's: whole pixels, never past the end. */
function page(height = 2000, view = 500) {
  const el = document.createElement('div');
  let top = 0;
  Object.defineProperty(el, 'scrollTop', {
    get: () => top,
    set: (v: number) => { top = Math.max(0, Math.min(height - view, Math.round(v))); },
  });
  Object.defineProperty(el, 'clientHeight', { get: () => view });
  Object.defineProperty(el, 'scrollHeight', { get: () => height });
  document.body.appendChild(el);
  return el;
}

describe('the scrolling step', () => {
  it('carries the part of a pixel that did not land, so slow speeds still move', () => {
    const el = page();
    let carry = 0;
    for (let i = 0; i < 60; i += 1) carry = advance(el, 10, 1000 / 60, carry);  // one second at 10 px/s
    expect(el.scrollTop).toBeGreaterThanOrEqual(9);
    expect(el.scrollTop).toBeLessThanOrEqual(10);
  });

  it('never lurches after a long pause between frames', () => {
    const el = page();
    advance(el, 80, 5000, 0);
    expect(el.scrollTop).toBe(8);  // a 100 ms frame at most
  });

  it('knows the end of the text', () => {
    const el = page();
    expect(atBottom(el)).toBe(false);
    el.scrollTop = 1500;
    expect(atBottom(el)).toBe(true);
  });

  it('keeps speeds in range and remembers the chosen one', () => {
    expect(clampSpeed(-3)).toBe(0);
    expect(clampSpeed(99)).toBe(SPEEDS.length - 1);
    localStorage.removeItem('jarvis_auto_scroll_speed');
    expect(readSpeed()).toBe(DEFAULT_SPEED);
    localStorage.setItem('jarvis_auto_scroll_speed', '5');
    expect(readSpeed()).toBe(5);
    localStorage.setItem('jarvis_auto_scroll_speed', 'nonsense');
    expect(readSpeed()).toBe(DEFAULT_SPEED);
  });
});

describe('the auto scroll control', () => {
  let frames: FrameRequestCallback[] = [];
  let now = 0;
  const run = (ms: number) => {
    for (let t = 0; t < ms; t += 16) {
      now += 16;
      const due = frames;
      frames = [];
      act(() => due.forEach((cb) => cb(now)));
    }
  };

  beforeEach(() => {
    frames = [];
    now = 1000;
    localStorage.removeItem('jarvis_auto_scroll_speed');
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { frames.push(cb); return frames.length; });
    vi.stubGlobal('cancelAnimationFrame', () => { frames = []; });
    vi.spyOn(performance, 'now').mockImplementation(() => now);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    document.body.innerHTML = '';
  });

  it('scrolls once started, at the chosen pace, and remembers the pace', () => {
    const el = page();
    render(<AutoScrollControl target={() => el} />);
    fireEvent.click(screen.getByTestId('auto-scroll-open'));
    run(1000);
    const slow = el.scrollTop;
    expect(slow).toBeGreaterThan(SPEEDS[DEFAULT_SPEED] * 0.8);
    fireEvent.click(screen.getByLabelText('Faster'));
    fireEvent.click(screen.getByLabelText('Faster'));
    expect(screen.getByTestId('auto-scroll-speed').textContent).toContain(`Speed ${DEFAULT_SPEED + 3}`);
    expect(localStorage.getItem('jarvis_auto_scroll_speed')).toBe(String(DEFAULT_SPEED + 2));
    run(1000);
    expect(el.scrollTop - slow).toBeGreaterThan(slow);
  });

  it('holds while the reader has the page and picks up after they let go', () => {
    const el = page();
    render(<AutoScrollControl target={() => el} />);
    fireEvent.click(screen.getByTestId('auto-scroll-open'));
    run(500);
    fireEvent.pointerDown(el);
    const held = el.scrollTop;
    run(1000);
    expect(el.scrollTop).toBe(held);
    expect(screen.getByTestId('auto-scroll').dataset.state).toBe('held');
    fireEvent.pointerUp(window);
    run(RESUME_AFTER_MS - 100);
    expect(el.scrollTop).toBe(held);
    run(600);
    expect(el.scrollTop).toBeGreaterThan(held);
  });

  it('holds while the caller pauses it, and pauses and closes on request', () => {
    const el = page();
    const view = render(<AutoScrollControl target={() => el} paused />);
    fireEvent.click(screen.getByTestId('auto-scroll-open'));
    run(800);
    expect(el.scrollTop).toBe(0);
    view.rerender(<AutoScrollControl target={() => el} />);
    run(800);
    const moved = el.scrollTop;
    expect(moved).toBeGreaterThan(0);
    fireEvent.click(screen.getByLabelText('Pause auto scroll'));
    run(800);
    expect(el.scrollTop).toBe(moved);
    fireEvent.click(screen.getByLabelText('Close auto scroll'));
    expect(screen.getByTestId('auto-scroll-open')).toBeTruthy();
  });

  it('stops at the end of the text and starts again from the top', () => {
    const el = page(700, 500);
    localStorage.setItem('jarvis_auto_scroll_speed', String(SPEEDS.length - 1));
    render(<AutoScrollControl target={() => el} />);
    fireEvent.click(screen.getByTestId('auto-scroll-open'));
    run(4000);
    expect(el.scrollTop).toBe(200);
    expect(screen.getByTestId('auto-scroll').dataset.state).toBe('end-of-the-text');
    fireEvent.click(screen.getByLabelText('Start again from the top'));
    expect(el.scrollTop).toBe(0);
  });
});
