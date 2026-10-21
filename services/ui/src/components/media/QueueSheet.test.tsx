import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import QueueSheet from './QueueSheet';
import { MediaPlayerContext } from '../../context/webPlayerContext';
import type { SharedPlayer } from '../../context/webPlayerContext';

const queueItems = [
  {
    queue_item_id: 'q1',
    name: 'First Song',
    duration: 120,
    available: true,
    media_item: { name: 'First Song', artists: [{ name: 'Artist One' }] },
  },
  {
    queue_item_id: 'q2',
    name: 'Second Song',
    duration: 180,
    available: true,
    media_item: { name: 'Second Song', artists: [{ name: 'Artist Two' }] },
  },
  {
    queue_item_id: 'q3',
    name: 'Third Song',
    duration: 240,
    available: true,
    media_item: { name: 'Third Song', artists: [{ name: 'Artist Three' }] },
  },
];

function player(over: Partial<SharedPlayer> = {}): SharedPlayer {
  return {
    playerId: 'web-player-1',
    mediaTitle: 'First Song',
    mediaArtist: 'Artist One',
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
    maCommand: vi.fn(async (command: string) => {
      if (command === 'player_queues/get') {
        return { current_index: 0, current_item: queueItems[0], items: queueItems.length };
      }
      if (command === 'player_queues/items') return queueItems;
      return {};
    }),
    ...over,
  } as unknown as SharedPlayer;
}

function renderSheet(value: SharedPlayer | null, props: Partial<{ open: boolean; onClose: () => void }> = {}) {
  return render(
    <MemoryRouter>
      <MediaPlayerContext.Provider value={value}>
        <QueueSheet open={props.open ?? true} onClose={props.onClose ?? (() => {})} />
      </MediaPlayerContext.Provider>
    </MemoryRouter>,
  );
}

describe('QueueSheet', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders nothing until it is opened', () => {
    renderSheet(player(), { open: false });
    expect(screen.queryByTestId('queue-sheet')).toBeNull();
  });

  it('shows the current track and the tracks up next', async () => {
    renderSheet(player());

    expect(screen.getByRole('dialog', { name: 'Queue' })).toBeTruthy();
    expect(await screen.findByText('First Song')).toBeTruthy();
    expect(await screen.findByText('Second Song')).toBeTruthy();
    expect(await screen.findByText('Third Song')).toBeTruthy();
    expect(screen.getByText('Playing · Artist One')).toBeTruthy();
  });

  it('removes an item through the queue API', async () => {
    let current = [...queueItems];
    const value = player({
      maCommand: vi.fn(async (command: string, args: Record<string, unknown> = {}) => {
        if (command === 'player_queues/get') {
          return { current_index: 0, current_item: current[0] ?? null, items: current.length };
        }
        if (command === 'player_queues/items') return current;
        if (command === 'player_queues/delete_item') {
          current = current.filter((item) => item.queue_item_id !== args.item_id_or_index);
        }
        return {};
      }),
    });
    renderSheet(value);

    await userEvent.click(await screen.findByRole('button', { name: 'Remove Second Song from the queue' }));
    await waitFor(() =>
      expect(value.maCommand).toHaveBeenCalledWith('player_queues/delete_item', {
        queue_id: 'web-player-1',
        item_id_or_index: 'q2',
      }),
    );
    await waitFor(() => expect(screen.queryByText('Second Song')).toBeNull());
  });

  it('asks for confirmation before clearing the queue', async () => {
    const value = player();
    renderSheet(value);

    const clear = await screen.findByRole('button', { name: 'Clear queue' });
    await userEvent.click(clear);
    expect(value.maCommand).not.toHaveBeenCalledWith('player_queues/clear', expect.anything());
    expect(screen.getByRole('button', { name: 'Clear queue?' })).toBeTruthy();

    await userEvent.click(screen.getByRole('button', { name: 'Clear queue?' }));
    await waitFor(() =>
      expect(value.maCommand).toHaveBeenCalledWith('player_queues/clear', { queue_id: 'web-player-1' }),
    );
  });

  it('shows an empty state when nothing is queued', async () => {
    const value = player({
      maCommand: vi.fn(async (command: string) => {
        if (command === 'player_queues/get') return { current_index: null, current_item: null, items: 0 };
        if (command === 'player_queues/items') return [];
        return {};
      }),
    });
    renderSheet(value);

    expect(await screen.findByText('The queue is empty.')).toBeTruthy();
  });

  it('surfaces a load failure without disabling the rest of the sheet', async () => {
    const value = player({
      maCommand: vi.fn(async () => {
        throw new Error('offline');
      }),
    });
    renderSheet(value);

    expect(await screen.findByText(/Could not load the queue/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Close queue' })).toBeTruthy();
  });

  it('closes on Escape without closing the now-playing sheet behind it', () => {
    const onClose = vi.fn();
    renderSheet(player(), { onClose });
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});
