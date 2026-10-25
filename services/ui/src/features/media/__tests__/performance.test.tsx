import { describe, expect, it, vi } from 'vitest';
import { act, render } from '@testing-library/react';
import { useLivePosition } from '../engine/useLivePosition';

const T0 = Date.parse('2026-10-24T12:00:00Z');

/**
 * P7-T4, the render half of the performance budget: while a track plays, the
 * running playhead re-renders only the component that asks for it (the
 * scrubber and its times), never the page around it.
 */
describe('P7-T4 media performance budget', () => {
  it('P7-T4 the page does not re-render as the playhead ticks; only the time does', () => {
    vi.useFakeTimers({ toFake: ['Date', 'requestAnimationFrame', 'cancelAnimationFrame', 'performance'] });
    vi.setSystemTime(T0);
    const anchor = {
      status: 'playing' as const,
      positionSec: 0,
      positionUpdatedAt: T0,
      item: { uri: '', title: 'A Song', artist: '', album: '', image: '', durationSec: 300 },
    };

    let pageRenders = 0;
    let timeRenders = 0;
    function Time() {
      timeRenders += 1;
      const position = useLivePosition(anchor);
      return <span>{position}</span>;
    }
    function Page() {
      pageRenders += 1;
      return <div><Time /></div>;
    }

    render(<Page />);
    expect(pageRenders).toBe(1);
    const timeAtMount = timeRenders;

    // Two seconds of playback, one act per frame batch as the browser would.
    for (let i = 0; i < 40; i += 1) act(() => { vi.advanceTimersByTime(50); });

    // The clock moved inside the time readout, but the page never re-rendered.
    expect(timeRenders).toBeGreaterThan(timeAtMount);
    expect(pageRenders).toBe(1);

    vi.useRealTimers();
  });
});
