import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../../../test/setup';
import { renderWithProviders } from '../../../test/render';
import bookFixture from '../../../../e2e/fixtures/media/media-item-book.json';
import { useMediaStore } from '../store/mediaStore';
import { PROGRESS_INTERVAL_MS, readOutput, useAbsProgressReporter } from '../engine/absProgress';
import type { SharedPlayer } from '../../../context/webPlayerContext';

const web = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
vi.mock('../../../context/MediaPlayerContext', () => ({ useSharedMAWebPlayer: () => web.state }));

import Media from '../../../pages/Media';

function player(over: Record<string, unknown> = {}) {
  return {
    isConnected: true, connectionState: 'CONNECTED', isPlaying: false, mediaTitle: null, mediaArtist: null, mediaImage: null,
    mediaUri: null, mediaFavorite: null, position: 0, duration: 0, volume: 70, muted: false, error: null, reconnectAttempts: 0,
    maxReconnectAttempts: 10, playerId: 'me', playbackRate: 1, connect: vi.fn(), reconnect: vi.fn(),
    play: vi.fn().mockResolvedValue(undefined), enqueue: vi.fn().mockResolvedValue(undefined), pause: vi.fn(), stop: vi.fn(),
    next: vi.fn(), previous: vi.fn(), seek: vi.fn().mockResolvedValue(undefined), setVolume: vi.fn(), setMuted: vi.fn(),
    setPlaybackRate: vi.fn(), maCommand: vi.fn(), listMaPlayers: vi.fn().mockResolvedValue([]),
    ...over,
  };
}

const renderAt = (route: string) => renderWithProviders(<Routes><Route path="/media/*" element={<Media />} /></Routes>, { route });

let progress: Record<string, unknown>[] = [];
beforeEach(() => {
  web.state = player();
  progress = [];
  server.use(
    http.get('/api/media/item', () => HttpResponse.json(bookFixture)),
    http.post('/api/media/ma-library-uri', () => HttpResponse.json({ ma_uri: 'library://audiobook/7', title: 'Project Hail Mary' })),
    http.post('/api/media/abs/progress', async ({ request }) => {
      progress.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({ status: 'SUCCESS', item_id: 'book-1', episode_id: null, message: 'ok' });
    }),
    http.post('/execute/media/status', () => HttpResponse.json({ status: 'SUCCESS', detail: { active: null, all_players: [] } })),
    http.get('/api/entities', () => HttpResponse.json({ entities: [] })),
  );
});
afterEach(() => {
  vi.useRealTimers();
});

describe('BookPage (P5-T5)', () => {
  it('shows the book, how far in, and resumes it on this device', async () => {
    renderAt('/media/book/book-1');
    expect(await screen.findByRole('heading', { name: 'Project Hail Mary', level: 1 })).toBeInTheDocument();
    expect(screen.getByText(/Andy Weir · 16:12:00/)).toBeInTheDocument();
    expect(await screen.findByText(/42% listened · 9h 23m left/)).toBeInTheDocument();
    expect(screen.getByText('A lone astronaut wakes up on a ship with no memory.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Resume' }));
    await waitFor(() => expect(web.state.play).toHaveBeenCalledWith('library://audiobook/7'));
    expect(useMediaStore.getState().absSession).toMatchObject({ itemId: 'book-1', outputId: '', durationSec: 58320 });
  });

  it('plays from a chapter by seeking to where it starts', async () => {
    renderAt('/media/book/book-1');
    const chapters = within(await screen.findByRole('list', { name: 'Chapters' }));
    expect(chapters.getByText('Starts at 30:00')).toBeInTheDocument();
    await userEvent.click(chapters.getByRole('button', { name: 'Play Chapter 3' }));
    await waitFor(() => expect(web.state.seek).toHaveBeenCalledWith(3900));
    expect(web.state.play).toHaveBeenCalledTimes(1);
  });

  it('while it plays here: −15 s, +30 s and the speed', async () => {
    web.state = player({ isPlaying: true, position: 600, mediaTitle: 'Project Hail Mary' });
    useMediaStore.getState().actions.setAbsSession({ itemId: 'book-1', title: 'Project Hail Mary', outputId: '', durationSec: 58320, startedAt: 1 });
    renderAt('/media/book/book-1');
    await userEvent.click(await screen.findByRole('button', { name: 'Back 15 seconds' }));
    expect(web.state.seek).toHaveBeenLastCalledWith(585);
    await userEvent.click(screen.getByRole('button', { name: 'Forward 30 seconds' }));
    expect(web.state.seek).toHaveBeenLastCalledWith(630);
    await userEvent.click(screen.getByRole('button', { name: 'Playback speed 1×' }));
    expect(web.state.setPlaybackRate).toHaveBeenCalledWith(1.25);
  });

  it('marks the book finished in Audiobookshelf', async () => {
    renderAt('/media/book/book-1');
    await userEvent.click(await screen.findByRole('button', { name: 'Mark finished' }));
    await waitFor(() => expect(progress).toEqual([{ item_id: 'book-1', current_time: 58320, duration: 58320, is_finished: true }]));
  });

  it('says when the book is gone or the server failed', async () => {
    server.use(http.get('/api/media/item', () => HttpResponse.json({ detail: 'x' }, { status: 404 })));
    const a = renderAt('/media/book/nope');
    expect(await screen.findByText('This book isn’t in your library', {}, { timeout: 5000 })).toBeInTheDocument();
    a.unmount();
    server.use(http.get('/api/media/item', () => HttpResponse.json({ detail: 'x' }, { status: 502 })));
    renderAt('/media/book/nope');
    expect(await screen.findByText('Couldn’t load this book', {}, { timeout: 5000 })).toBeInTheDocument();
  });
});

describe('Audiobookshelf progress write-back (journey 8)', () => {
  function Reporter({ p }: { p: SharedPlayer }) {
    useAbsProgressReporter(p);
    return null;
  }

  it('saves the position every 15 s while playing here, and at once on pause', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    useMediaStore.getState().actions.setAbsSession({ itemId: 'book-1', title: 'B', outputId: '', durationSec: 1000, startedAt: 1 });
    const p = { isPlaying: true, position: 100, duration: 1000, mediaUri: 'library://audiobook/7', mediaTitle: 'B' } as unknown as SharedPlayer;
    const { rerender } = render(<Reporter p={p} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(PROGRESS_INTERVAL_MS); });
    await waitFor(() => expect(progress).toEqual([{ item_id: 'book-1', current_time: 100, duration: 1000 }]));
    rerender(<Reporter p={{ ...p, isPlaying: false, position: 112 } as SharedPlayer} />);
    await waitFor(() => expect(progress.at(-1)).toEqual({ item_id: 'book-1', current_time: 112, duration: 1000 }));
  });

  it('saves a remote speaker\'s position, and stops once it plays something else', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { actions } = useMediaStore.getState();
    actions.ingest({ type: 'player', output_id: 'ma:office', state: 'playing', item: { uri: 'library://audiobook/7', title: 'B', duration: 1000 }, position: 50 });
    actions.setAbsSession({ itemId: 'book-1', title: 'B', outputId: 'ma:office', durationSec: null, startedAt: 1 });
    render(<Reporter p={null as unknown as SharedPlayer} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(PROGRESS_INTERVAL_MS); });
    await waitFor(() => expect(progress[0]).toMatchObject({ item_id: 'book-1', duration: 1000 }));
    act(() => actions.ingest({ type: 'player', output_id: 'ma:office', item: { uri: 'library://track/1', title: 'Song' } }));
    await act(async () => { await vi.advanceTimersByTimeAsync(PROGRESS_INTERVAL_MS); });
    expect(useMediaStore.getState().absSession).toBeNull();
  });

  it('reads nothing for an output it does not know', () => {
    const session = { itemId: 'x', title: 'x', outputId: 'ma:gone', durationSec: null, startedAt: 1 };
    expect(readOutput(session, null, 0)).toBeNull();
    expect(readOutput({ ...session, outputId: '' }, null, 0)).toBeNull();
  });
});
