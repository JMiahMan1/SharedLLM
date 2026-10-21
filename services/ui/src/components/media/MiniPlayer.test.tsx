import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import MiniPlayer from './MiniPlayer';
import { MediaPlayerContext } from '../../context/webPlayerContext';
import type { SharedPlayer } from '../../context/webPlayerContext';

function player(over: Partial<SharedPlayer> = {}): SharedPlayer {
  return {
    mediaTitle: 'A Song',
    mediaArtist: 'The Artist',
    isPlaying: true,
    mediaImage: null,
    position: 30,
    duration: 120,
    play: vi.fn(),
    pause: vi.fn(),
    next: vi.fn(),
    previous: vi.fn(),
    ...over,
  } as unknown as SharedPlayer;
}

function renderAt(path: string, value: SharedPlayer | null, onOpen?: () => void) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <MediaPlayerContext.Provider value={value}>
        <MiniPlayer onOpen={onOpen} />
      </MediaPlayerContext.Provider>
    </MemoryRouter>
  );
}

describe('MiniPlayer', () => {
  it('shows what is playing and controls it from another page', async () => {
    const value = player();
    renderAt('/bible', value);

    expect(screen.getByTestId('mini-player')).toBeTruthy();
    expect(screen.getByText('A Song')).toBeTruthy();
    expect(screen.getByText('The Artist')).toBeTruthy();

    await userEvent.click(screen.getByRole('button', { name: 'Pause' }));
    expect(value.pause).toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Next track' }));
    expect(value.next).toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Previous track' }));
    expect(value.previous).toHaveBeenCalled();
  });

  it('stays out of the way on the Media page', () => {
    renderAt('/media', player());
    expect(screen.queryByTestId('mini-player')).toBeNull();
  });

  it('opens the full now-playing sheet when tapped', async () => {
    const onOpen = vi.fn();
    renderAt('/bible', player(), onOpen);
    await userEvent.click(screen.getByRole('button', { name: 'Open now playing' }));
    expect(onOpen).toHaveBeenCalled();
  });

  it('renders nothing when the player is idle', () => {
    renderAt('/bible', player({ mediaTitle: null }));
    expect(screen.queryByTestId('mini-player')).toBeNull();
  });
});
