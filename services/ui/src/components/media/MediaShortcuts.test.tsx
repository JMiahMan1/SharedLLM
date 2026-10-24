import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import MediaShortcuts from './MediaShortcuts';
import { MediaPlayerContext } from '../../context/webPlayerContext';
import type { SharedPlayer } from '../../context/webPlayerContext';

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
    play: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn(),
    next: vi.fn().mockResolvedValue(undefined),
    previous: vi.fn().mockResolvedValue(undefined),
    seek: vi.fn().mockResolvedValue(undefined),
    setVolume: vi.fn(),
    setMuted: vi.fn(),
    setPlaybackRate: vi.fn(),
    maCommand: vi.fn(async (command: string) => {
      if (command === 'player_queues/get') return { shuffle_enabled: false, repeat_mode: 'off' };
      return {};
    }),
    ...over,
  } as unknown as SharedPlayer;
}

function renderShortcuts(
  value: SharedPlayer | null,
  handlers: Partial<{ onOpenNowPlaying: () => void; onOpenQueue: () => void }> = {},
) {
  return render(
    <MemoryRouter>
      <MediaPlayerContext.Provider value={value}>
        <input data-global-search aria-label="Global search" />
        <MediaShortcuts
          onOpenNowPlaying={handlers.onOpenNowPlaying ?? (() => {})}
          onOpenQueue={handlers.onOpenQueue ?? (() => {})}
        />
      </MediaPlayerContext.Provider>
    </MemoryRouter>,
  );
}

describe('MediaShortcuts', () => {
  it('toggles playback with Space', () => {
    const value = player();
    renderShortcuts(value);

    fireEvent.keyDown(document, { key: ' ' });
    expect(value.pause).toHaveBeenCalled();
  });

  it('seeks 5 seconds, or 30 with Shift', () => {
    const value = player();
    renderShortcuts(value);

    fireEvent.keyDown(document, { key: 'ArrowRight' });
    expect(value.seek).toHaveBeenLastCalledWith(35);

    fireEvent.keyDown(document, { key: 'ArrowLeft' });
    expect(value.seek).toHaveBeenLastCalledWith(25);

    fireEvent.keyDown(document, { key: 'ArrowRight', shiftKey: true });
    expect(value.seek).toHaveBeenLastCalledWith(60);
  });

  it('changes volume, mutes, and skips tracks', () => {
    const value = player();
    renderShortcuts(value);

    fireEvent.keyDown(document, { key: 'ArrowUp' });
    expect(value.setVolume).toHaveBeenLastCalledWith(75);

    fireEvent.keyDown(document, { key: 'ArrowDown' });
    expect(value.setVolume).toHaveBeenLastCalledWith(65);

    fireEvent.keyDown(document, { key: 'm' });
    expect(value.setMuted).toHaveBeenCalledWith(true);

    fireEvent.keyDown(document, { key: 'n' });
    expect(value.next).toHaveBeenCalled();

    fireEvent.keyDown(document, { key: 'p' });
    expect(value.previous).toHaveBeenCalled();
  });

  it('toggles shuffle and cycles repeat through the queue API', async () => {
    const value = player();
    renderShortcuts(value);

    fireEvent.keyDown(document, { key: 's' });
    await waitFor(() =>
      expect(value.maCommand).toHaveBeenCalledWith('player_queues/shuffle', {
        queue_id: 'web-player-1',
        shuffle_enabled: true,
      }),
    );

    fireEvent.keyDown(document, { key: 'r' });
    await waitFor(() =>
      expect(value.maCommand).toHaveBeenCalledWith('player_queues/repeat', {
        queue_id: 'web-player-1',
        repeat_mode: 'all',
      }),
    );
  });

  it('opens Now Playing and the queue', () => {
    const onOpenNowPlaying = vi.fn();
    const onOpenQueue = vi.fn();
    renderShortcuts(player(), { onOpenNowPlaying, onOpenQueue });

    fireEvent.keyDown(document, { key: 'f' });
    expect(onOpenNowPlaying).toHaveBeenCalled();

    fireEvent.keyDown(document, { key: 'q' });
    expect(onOpenQueue).toHaveBeenCalled();
  });

  it('leaves typing and focused buttons alone', () => {
    const value = player();
    renderShortcuts(value);

    const input = screen.getByRole('textbox', { name: 'Global search' });
    input.focus();
    fireEvent.keyDown(input, { key: 'm' });
    expect(value.setMuted).not.toHaveBeenCalled();

    const button = document.createElement('button');
    document.body.appendChild(button);
    button.focus();
    fireEvent.keyDown(button, { key: ' ' });
    expect(value.pause).not.toHaveBeenCalled();
    button.remove();
  });

  it('focuses the header search with /', () => {
    renderShortcuts(player());
    const search = screen.getByRole('textbox', { name: 'Global search' });
    fireEvent.keyDown(document, { key: '/' });
    expect(document.activeElement).toBe(search);
  });

  it('shows the shortcut help and closes it with Escape', async () => {
    renderShortcuts(player());

    fireEvent.keyDown(document, { key: '?' });
    expect(await screen.findByRole('dialog', { name: 'Keyboard shortcuts' })).toBeTruthy();
    expect(screen.getByText('Play or pause')).toBeTruthy();
    expect(screen.getByText('Open the queue')).toBeTruthy();

    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Keyboard shortcuts' })).toBeNull());
  });

  it('does nothing without a player', () => {
    renderShortcuts(null);
    expect(() => fireEvent.keyDown(document, { key: ' ' })).not.toThrow();
    expect(screen.queryByTestId('shortcut-help')).toBeNull();
  });

  it('leaves transport alone when nothing is playing', () => {
    const value = player({ mediaTitle: null });
    renderShortcuts(value);
    fireEvent.keyDown(document, { key: ' ' });
    fireEvent.keyDown(document, { key: 'm' });
    expect(value.play).not.toHaveBeenCalled();
    expect(value.pause).not.toHaveBeenCalled();
    expect(value.setMuted).not.toHaveBeenCalled();
  });

  it('on a media page, / focuses the media search and ⌘K/Ctrl+K opens search', () => {
    const Where = () => <p data-testid="where">{useLocation().pathname}</p>;
    render(
      <MemoryRouter initialEntries={['/media']}>
        <MediaPlayerContext.Provider value={player()}>
          <input data-global-search aria-label="Global search" />
          <Routes><Route path="/media" element={<input data-media-search aria-label="Media search" />} /></Routes>
          <MediaShortcuts onOpenNowPlaying={() => {}} onOpenQueue={() => {}} />
          <Where />
        </MediaPlayerContext.Provider>
      </MemoryRouter>,
    );
    fireEvent.keyDown(document, { key: '/' });
    expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'Media search' }));
    fireEvent.keyDown(document.activeElement!, { key: 'k', ctrlKey: true });
    expect(screen.getByTestId('where')).toHaveTextContent('/media/search');
  });

  it('leaves Ctrl+K alone outside media', () => {
    renderShortcuts(player());
    const event = new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true, cancelable: true });
    document.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
  });

  it('still searches and shows help without a player', async () => {
    renderShortcuts(null);
    const search = screen.getByRole('textbox', { name: 'Global search' });
    fireEvent.keyDown(document, { key: '/' });
    expect(document.activeElement).toBe(search);

    fireEvent.keyDown(document, { key: '?' });
    expect(await screen.findByRole('dialog', { name: 'Keyboard shortcuts' })).toBeTruthy();
  });
});
