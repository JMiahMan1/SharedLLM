import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { NowPlayingAnnouncer } from './NowPlayingAnnouncer';
import { MediaPlayerContext, type SharedPlayer } from '../../../context/webPlayerContext';
import { useMediaStore } from '../store/mediaStore';
import type { RemotePlayer } from '../types';

function player(over: Partial<SharedPlayer> = {}): SharedPlayer {
  return {
    playerId: 'me',
    mediaTitle: 'A Song',
    mediaArtist: 'The Artist',
    mediaUri: 'library://track/1',
    mediaImage: null,
    isPlaying: true,
    position: 30,
    duration: 120,
    volume: 70,
    muted: false,
    playbackRate: 1,
    play: vi.fn(),
    pause: vi.fn(),
    next: vi.fn(),
    previous: vi.fn(),
    seek: vi.fn(),
    setVolume: vi.fn(),
    setMuted: vi.fn(),
    setPlaybackRate: vi.fn(),
    maCommand: vi.fn().mockResolvedValue({}),
    ...over,
  } as unknown as SharedPlayer;
}

function remote(over: Partial<RemotePlayer> = {}): RemotePlayer {
  return {
    id: 'media_player.kitchen',
    kind: 'ha',
    name: 'Kitchen',
    available: true,
    status: 'playing',
    item: { uri: 'library://track/9', title: 'Midnight Drive', artist: '', album: '', image: '', durationSec: 200 },
    positionSec: 0,
    positionUpdatedAt: 0,
    volume: 40,
    muted: false,
    shuffle: null,
    repeat: null,
    groupMembers: [],
    iconKind: 'speaker',
    maPlayerId: null,
    appName: null,
    queueVersion: 0,
    ...over,
  };
}

function renderAnnouncer(value: SharedPlayer | null) {
  return render(
    <MediaPlayerContext.Provider value={value}>
      <NowPlayingAnnouncer />
    </MediaPlayerContext.Provider>,
  );
}

function liveText(): string {
  return screen.getByTestId('now-playing-announcer').textContent ?? '';
}

afterEach(() => {
  vi.useRealTimers();
});

describe('P7-T3 NowPlayingAnnouncer', () => {
  it('announces the track and where it plays on this device', () => {
    renderAnnouncer(player());
    expect(liveText()).toBe('Now playing A Song on This device');
    const region = screen.getByTestId('now-playing-announcer');
    expect(region).toHaveAttribute('aria-live', 'polite');
  });

  it('announces a state change once, not on every position tick', () => {
    const { rerender } = renderAnnouncer(player());
    expect(liveText()).toBe('Now playing A Song on This device');

    // A new position reading is not a new announcement.
    rerender(
      <MediaPlayerContext.Provider value={player({ position: 45 })}>
        <NowPlayingAnnouncer />
      </MediaPlayerContext.Provider>,
    );
    expect(liveText()).toBe('Now playing A Song on This device');

    // Pausing is.
    rerender(
      <MediaPlayerContext.Provider value={player({ position: 45, isPlaying: false })}>
        <NowPlayingAnnouncer />
      </MediaPlayerContext.Provider>,
    );
    expect(liveText()).toBe('Paused A Song on This device');
  });

  it('announces the active remote output', () => {
    act(() => {
      useMediaStore.setState({ activeOutputId: 'media_player.kitchen', players: { 'media_player.kitchen': remote() } });
    });
    renderAnnouncer(player());
    expect(liveText()).toBe('Now playing Midnight Drive on Kitchen');
  });

  it('says nothing when nothing is playing', () => {
    renderAnnouncer(player({ mediaTitle: null }));
    expect(liveText()).toBe('');
  });
});
