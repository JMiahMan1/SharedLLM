import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, act } from '@testing-library/react';
import { useState } from 'react';

const h = vi.hoisted(() => ({
  native: false,
  mounts: 0,
  player: {
    isConnected: true,
    isPlaying: true,
    mediaTitle: 'Narnia',
    mediaArtist: 'C. S. Lewis',
    play: vi.fn(),
    pause: vi.fn(),
    next: vi.fn(),
    previous: vi.fn(),
  },
  update: vi.fn().mockResolvedValue(undefined),
  stop: vi.fn().mockResolvedValue(undefined),
  actionListener: null as null | ((d: { action: string }) => void),
}));

vi.mock('@capacitor/core', async (orig) => ({
  ...(await orig<typeof import('@capacitor/core')>()),
  Capacitor: { isNativePlatform: () => h.native },
}));
vi.mock('../lib/maWebPlayer', () => ({
  useMAWebPlayer: () => {
    // One player for the app's lifetime: counted on first render only.
    const [p] = useState(() => {
      h.mounts += 1;
      return h.player;
    });
    return p;
  },
}));
vi.mock('../plugins/jarvisMedia', () => ({
  default: {
    update: h.update,
    stop: h.stop,
    addListener: (_e: string, cb: (d: { action: string }) => void) => {
      h.actionListener = cb;
      return Promise.resolve({ remove: vi.fn() });
    },
  },
}));

import { MediaPlayerProvider, useSharedMAWebPlayer } from '../context/MediaPlayerContext';

function MediaPage({ onState }: { onState: (s: unknown) => void }) {
  useSharedMAWebPlayer(onState);
  return <p>media</p>;
}

function App({ page, onState }: { page: 'media' | 'bible'; onState: (s: unknown) => void }) {
  return <MediaPlayerProvider>{page === 'media' ? <MediaPage onState={onState} /> : <p>bible</p>}</MediaPlayerProvider>;
}

beforeEach(() => {
  h.native = false;
  h.mounts = 0;
  h.actionListener = null;
  vi.clearAllMocks();
});

describe('the app-wide web player', () => {
  it('outlives the Media page and replays its state when the page comes back', () => {
    const onState = vi.fn();
    const { rerender } = render(<App page="media" onState={onState} />);
    rerender(<App page="bible" onState={onState} />);
    onState.mockClear();
    rerender(<App page="media" onState={onState} />);
    expect(h.mounts).toBe(1);
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({ isPlaying: true, mediaTitle: 'Narnia' }));
  });

  it('keeps Android playback alive and routes the lock-screen buttons back', async () => {
    h.native = true;
    render(<App page="bible" onState={vi.fn()} />);
    expect(h.update).toHaveBeenCalledWith({ title: 'Narnia', artist: 'C. S. Lewis', playing: true });
    await act(async () => {});
    h.actionListener?.({ action: 'pause' });
    h.actionListener?.({ action: 'next' });
    expect(h.player.pause).toHaveBeenCalled();
    expect(h.player.next).toHaveBeenCalled();
  });

  it('needs the provider', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => render(<MediaPage onState={vi.fn()} />)).toThrow(/MediaPlayerProvider/);
    spy.mockRestore();
  });
});
