import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, act, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { useState } from 'react';

const h = vi.hoisted(() => ({
  native: false,
  mounts: 0,
  player: {} as Record<string, unknown>,
  update: vi.fn().mockResolvedValue(undefined),
  stop: vi.fn().mockResolvedValue(undefined),
  reclaim: vi.fn().mockResolvedValue({ playing: false }),
  actionListener: null as null | ((d: { action: string }) => void),
}));

vi.mock('@capacitor/core', async (orig) => ({
  ...(await orig<typeof import('@capacitor/core')>()),
  Capacitor: { isNativePlatform: () => h.native },
}));
vi.mock('../lib/maWebPlayer', () => ({
  clientName: () => "Jeremiah's Web Player (Mobile)",
  useMAWebPlayer: () => {
    // One player for the app's lifetime: counted on first render only. Its
    // state is whatever the test last put in h.player.
    useState(() => {
      h.mounts += 1;
      return null;
    });
    return h.player;
  },
}));
vi.mock('../plugins/jarvisMedia', () => ({
  default: {
    update: h.update,
    stop: h.stop,
    reclaim: h.reclaim,
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

const PLAYING = {
  isConnected: true,
  isPlaying: true,
  mediaTitle: 'Narnia',
  mediaArtist: 'C. S. Lewis',
  mediaUri: 'library://audiobook/260',
  position: 42,
  duration: 3600,
  volume: 70,
  muted: false,
  playerId: 'player-1',
};

beforeEach(() => {
  h.player = { ...PLAYING, play: vi.fn().mockResolvedValue(undefined), pause: vi.fn(), next: vi.fn(), previous: vi.fn() };
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
    expect(h.update).toHaveBeenCalledWith({
      title: 'Narnia', artist: 'C. S. Lewis', playing: true,
      playerId: 'player-1', playerName: "Jeremiah's Web Player (Mobile)", volume: 70,
    });
    await act(async () => {});
    h.actionListener?.({ action: 'pause' });
    h.actionListener?.({ action: 'next' });
    expect(h.player.pause).toHaveBeenCalled();
    expect(h.player.next).toHaveBeenCalled();
  });

  it('takes playback back from the native player when the app opens again', async () => {
    h.native = true;
    h.reclaim.mockResolvedValueOnce({ playing: true });
    render(<App page="bible" onState={vi.fn()} />);
    await waitFor(() => expect(h.player.play).toHaveBeenCalled());
    expect(h.reclaim).toHaveBeenCalledTimes(1);
  });

  it('does not start playing at launch when nothing was playing natively', async () => {
    h.native = true;
    render(<App page="bible" onState={vi.fn()} />);
    await waitFor(() => expect(h.reclaim).toHaveBeenCalled());
    expect(h.player.play).not.toHaveBeenCalled();
  });

  it('needs the provider', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => render(<MediaPage onState={vi.fn()} />)).toThrow(/MediaPlayerProvider/);
    spy.mockRestore();
  });
});

describe('reporting the web player to the house', () => {
  let reports: Array<Record<string, unknown>> = [];
  beforeEach(() => {
    reports = [];
    server.use(
      http.post('/execute/media/state/sync', async ({ request }) => {
        reports.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ status: 'SUCCESS' });
      }),
    );
  });

  it('reports what is playing from any page, not just Media', async () => {
    render(<App page="bible" onState={vi.fn()} />);
    await waitFor(() => expect(reports.length).toBeGreaterThan(0));
    expect(reports[0]).toMatchObject({
      entity_id: 'web_player',
      state: 'playing',
      media_title: 'Narnia',
      media_type: 'audiobook',
      media_content_id: 'library://audiobook/260',
    });
  });

  it('keeps reporting while it plays, so the server can tell a closed app from a playing one', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<App page="bible" onState={vi.fn()} />);
      await act(async () => { await vi.advanceTimersByTimeAsync(31_000); });
      await waitFor(() => expect(reports.filter((r) => r.state === 'playing').length).toBeGreaterThanOrEqual(3));
    } finally {
      vi.useRealTimers();
    }
  });

  it('says nothing for a player that never played (another device may be playing)', async () => {
    h.player = { ...h.player, isPlaying: false, mediaTitle: null, mediaUri: null, isConnected: false };
    render(<App page="bible" onState={vi.fn()} />);
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    expect(reports).toEqual([]);
  });

  it('reports paused, then idle once stopped', async () => {
    const { rerender } = render(<App page="bible" onState={vi.fn()} />);
    await waitFor(() => expect(reports.at(-1)?.state).toBe('playing'));
    h.player = { ...h.player, isPlaying: false };
    rerender(<App page="media" onState={vi.fn()} />);
    await waitFor(() => expect(reports.at(-1)?.state).toBe('paused'));
    h.player = { ...h.player, mediaTitle: null, mediaUri: null };
    rerender(<App page="bible" onState={vi.fn()} />);
    await waitFor(() => expect(reports.at(-1)?.state).toBe('idle'));
  });

  it('drops the Android notification when playback is stopped', async () => {
    h.native = true;
    const { rerender } = render(<App page="bible" onState={vi.fn()} />);
    expect(h.update).toHaveBeenCalled();
    h.player = { ...h.player, isPlaying: false, mediaTitle: null };
    rerender(<App page="media" onState={vi.fn()} />);
    expect(h.stop).toHaveBeenCalled();
  });

  it('does not touch the Android service at start-up when nothing has played', () => {
    h.native = true;
    h.player = { ...h.player, isPlaying: false, mediaTitle: null, isConnected: false };
    render(<App page="bible" onState={vi.fn()} />);
    expect(h.update).not.toHaveBeenCalled();
    expect(h.stop).not.toHaveBeenCalled();
  });
});

describe('one tab plays (BUG-66)', () => {
  it('drops this tab\'s audio when another tab starts playing, and says so', async () => {
    const { FakeBroadcastChannel } = await import('./fakes/FakeBroadcastChannel');
    const { TabLock, CHANNEL_NAME } = await import('../features/media/engine/tabLock');
    const { useMediaStore } = await import('../features/media/store/mediaStore');
    FakeBroadcastChannel.reset();
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel);
    const disconnect = vi.fn();
    h.player = { ...h.player, disconnect };
    render(<App page="bible" onState={vi.fn()} />);
    const other = new TabLock({ channel: new FakeBroadcastChannel(CHANNEL_NAME), tabId: 'other-tab' });
    // This tab is playing, so it holds the lock; another tab now plays. The
    // claim must land in a later millisecond: two claims in the same tick are
    // tie-broken by tab id, and this test's fixed id can sort either side of
    // the app's random one (the flake that reddened CI).
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 2));
      other.claim();
    });
    expect(disconnect).toHaveBeenCalledTimes(1);
    expect(useMediaStore.getState().playingInOtherTab).toBe(true);
    other.dispose();
    vi.unstubAllGlobals();
  });
});
