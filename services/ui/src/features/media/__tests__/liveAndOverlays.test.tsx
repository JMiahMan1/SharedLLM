import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, renderHook, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { useLivePosition, LIVE_POSITION_INTERVAL_MS } from '../engine/useLivePosition';
import { useMediaStore } from '../store/mediaStore';
import { MediaPlayerContext, type SharedPlayer } from '../../../context/webPlayerContext';
import MediaOverlays from '../components/MediaOverlays';

const T0 = Date.parse('2026-10-24T12:00:00Z');

afterEach(() => {
  vi.useRealTimers();
});

describe('useLivePosition', () => {
  const anchor = (status: 'playing' | 'paused') => ({
    status, positionSec: 10, positionUpdatedAt: T0, item: { uri: '', title: '', artist: '', album: '', image: '', durationSec: 100 },
  });

  it('runs the clock four times a second while playing, re-rendering only its own component', () => {
    vi.useFakeTimers({ toFake: ['Date', 'requestAnimationFrame', 'cancelAnimationFrame', 'performance'] });
    vi.setSystemTime(T0);
    let renders = 0;
    const { result } = renderHook(() => {
      renders += 1;
      return useLivePosition(anchor('playing'));
    });
    expect(result.current).toBe(10);
    const before = renders;
    // One act per 50 ms frame batch, so React renders as the browser would.
    for (let i = 0; i < 20; i += 1) act(() => { vi.advanceTimersByTime(50); });
    expect(result.current).toBeCloseTo(11, 0);
    // About 4 renders in a second: the clock, never a per-frame storm.
    expect(renders - before).toBeLessThanOrEqual(Math.ceil(1000 / LIVE_POSITION_INTERVAL_MS) + 1);
    expect(renders - before).toBeGreaterThanOrEqual(3);
  });

  it('stands still (no re-renders) while paused, and is 0 without an anchor', () => {
    vi.useFakeTimers({ toFake: ['Date', 'requestAnimationFrame', 'cancelAnimationFrame', 'performance'] });
    vi.setSystemTime(T0);
    let renders = 0;
    const { result, rerender } = renderHook(({ a }) => {
      renders += 1;
      return useLivePosition(a);
    }, { initialProps: { a: anchor('paused') as ReturnType<typeof anchor> | null } });
    act(() => { vi.advanceTimersByTime(5000); });
    expect(result.current).toBe(10);
    expect(renders).toBe(1);
    rerender({ a: null });
    expect(result.current).toBe(0);
  });
});

describe('MediaOverlays', () => {
  const player = {
    mediaTitle: 'A Song', mediaArtist: 'Artist', mediaImage: null, mediaUri: null, isPlaying: true, position: 10, duration: 100,
    volume: 50, muted: false, playbackRate: 1, playerId: 'me', isConnected: true, connectionState: 'CONNECTED',
    play: vi.fn(), pause: vi.fn(), next: vi.fn(), previous: vi.fn(), seek: vi.fn(), setVolume: vi.fn(), setMuted: vi.fn(),
    setPlaybackRate: vi.fn(), maCommand: vi.fn().mockResolvedValue({}),
  } as unknown as SharedPlayer;

  it('opens Now Playing from the mini player through the store', () => {
    render(
      <MemoryRouter initialEntries={['/bible']}>
        <MediaPlayerContext.Provider value={player}>
          <MediaOverlays />
        </MediaPlayerContext.Provider>
      </MemoryRouter>,
    );
    expect(screen.queryByTestId('now-playing-sheet')).toBeNull();
    act(() => screen.getByRole('button', { name: 'Open now playing' }).click());
    expect(useMediaStore.getState().sheets.nowPlaying).toBe(true);
    expect(screen.getByTestId('now-playing-sheet')).toBeTruthy();
    act(() => useMediaStore.getState().actions.openSheet('queue'));
    expect(useMediaStore.getState().sheets.queue).toBe(true);
  });
});
