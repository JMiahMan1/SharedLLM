import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import NowPlayingSheet from './NowPlayingSheet';
import { MediaPlayerContext } from '../../context/webPlayerContext';
import type { SharedPlayer } from '../../context/webPlayerContext';

vi.mock('../../services/api', () => ({
  api: { setMediaFavorite: vi.fn().mockResolvedValue({ status: 'ok', favorite: true }) },
}));

import { api } from '../../services/api';

function player(over: Partial<SharedPlayer> = {}): SharedPlayer {
  return {
    playerId: 'web-player-1',
    mediaTitle: 'A Song',
    mediaArtist: 'The Artist',
    mediaUri: 'library://track/1',
    mediaImage: null,
    mediaFavorite: false,
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
    seek: vi.fn().mockResolvedValue(undefined),
    setVolume: vi.fn(),
    setMuted: vi.fn(),
    setPlaybackRate: vi.fn(),
    maCommand: vi.fn().mockResolvedValue({}),
    ...over,
  } as unknown as SharedPlayer;
}

function renderSheet(value: SharedPlayer | null, props: Partial<{ open: boolean; onClose: () => void }> = {}) {
  return render(
    <MemoryRouter>
      <MediaPlayerContext.Provider value={value}>
        <NowPlayingSheet open={props.open ?? true} onClose={props.onClose ?? (() => {})} />
      </MediaPlayerContext.Provider>
    </MemoryRouter>,
  );
}

describe('NowPlayingSheet', () => {
  beforeEach(() => {
    vi.mocked(api.setMediaFavorite).mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders nothing until it is opened', () => {
    renderSheet(player(), { open: false });
    expect(screen.queryByTestId('now-playing-sheet')).toBeNull();
  });

  it('shows the track and controls playback', async () => {
    const value = player();
    renderSheet(value);

    expect(screen.getByRole('dialog', { name: 'Now playing' })).toBeTruthy();
    expect(screen.getByText('A Song')).toBeTruthy();
    expect(screen.getByText('The Artist')).toBeTruthy();
    expect(screen.getByText('-1:30')).toBeTruthy();

    await userEvent.click(screen.getByRole('button', { name: 'Pause' }));
    expect(value.pause).toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Next track' }));
    expect(value.next).toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Previous track' }));
    expect(value.previous).toHaveBeenCalled();
  });

  it('commits a seek when the finger or key is released', () => {
    const value = player();
    renderSheet(value);

    const scrubber = screen.getByRole('slider', { name: 'Playback position' });
    fireEvent.change(scrubber, { target: { value: '75' } });
    expect(value.seek).not.toHaveBeenCalled();
    fireEvent.pointerUp(scrubber);
    expect(value.seek).toHaveBeenCalledWith(75);
  });

  it('sets the volume and toggles mute', async () => {
    const value = player();
    renderSheet(value);

    fireEvent.change(screen.getByRole('slider', { name: 'Volume' }), { target: { value: '40' } });
    expect(value.setVolume).toHaveBeenCalledWith(40);

    await userEvent.click(screen.getByRole('button', { name: 'Mute' }));
    expect(value.setMuted).toHaveBeenCalledWith(true);
  });

  it('favorites the current track', async () => {
    renderSheet(player());
    await userEvent.click(screen.getByRole('button', { name: 'Add to favorites' }));
    expect(api.setMediaFavorite).toHaveBeenCalledWith('library://track/1', true);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Remove from favorites' })).toBeTruthy());
  });

  it('offers book controls and a playback speed for audiobooks', async () => {
    const value = player({ mediaUri: 'library://audiobook/7', position: 90, duration: 300 });
    renderSheet(value);

    await userEvent.click(screen.getByRole('button', { name: 'Forward 30 seconds' }));
    expect(value.seek).toHaveBeenCalledWith(120);

    await userEvent.click(screen.getByRole('button', { name: 'Back 15 seconds' }));
    expect(value.seek).toHaveBeenCalledWith(75);

    await userEvent.click(screen.getByRole('button', { name: 'Playback speed' }));
    expect(value.setPlaybackRate).toHaveBeenCalledWith(1.25);
  });

  it('toggles shuffle and repeat through the queue API', async () => {
    const value = player();
    renderSheet(value);

    await waitFor(() => expect(value.maCommand).toHaveBeenCalledWith('player_queues/get', { queue_id: 'web-player-1' }));
    await userEvent.click(screen.getByRole('button', { name: 'Shuffle' }));
    await waitFor(() =>
      expect(value.maCommand).toHaveBeenCalledWith('player_queues/shuffle', { queue_id: 'web-player-1', shuffle_enabled: true }),
    );

    await userEvent.click(screen.getByRole('button', { name: 'Repeat off' }));
    await waitFor(() =>
      expect(value.maCommand).toHaveBeenCalledWith('player_queues/repeat', { queue_id: 'web-player-1', repeat_mode: 'all' }),
    );
  });

  it('opens the queue sheet from the bottom row', async () => {
    renderSheet(player());
    await userEvent.click(screen.getByRole('button', { name: 'Open queue' }));
    expect(await screen.findByRole('dialog', { name: 'Queue' })).toBeTruthy();
  });

  it('closes on Escape', () => {
    const onClose = vi.fn();
    renderSheet(player(), { onClose });
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('pauses when the sleep timer reaches the end of the track', () => {
    vi.useFakeTimers();
    const value = player({ position: 90, duration: 100 });
    renderSheet(value);

    fireEvent.click(screen.getByRole('button', { name: 'Sleep timer' }));
    fireEvent.click(screen.getByRole('button', { name: 'End of track' }));
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(value.pause).toHaveBeenCalled();
  });
});
