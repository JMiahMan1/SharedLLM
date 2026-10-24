import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../../../test/setup';
import { useMediaStore } from '../store/mediaStore';
import { usePlayback, VOLUME_DEBOUNCE_MS } from '../engine/usePlayback';
import type { WebPlayerHandle } from '../engine/adapters/adapters';

type Sent = { command: string; args: Record<string, unknown> };
let sent: Sent[] = [];
let refuse = false;

const web: WebPlayerHandle = {
  playerId: 'me',
  play: vi.fn().mockResolvedValue(undefined),
  enqueue: vi.fn().mockResolvedValue(undefined),
  pause: vi.fn().mockResolvedValue(undefined),
  next: vi.fn().mockResolvedValue(undefined),
  previous: vi.fn().mockResolvedValue(undefined),
  stop: vi.fn().mockResolvedValue(undefined),
  seek: vi.fn().mockResolvedValue(undefined),
  setVolume: vi.fn(),
  setMuted: vi.fn(),
  maCommand: vi.fn().mockResolvedValue({}),
};

beforeEach(() => {
  sent = [];
  refuse = false;
  server.use(http.post('/api/media/ma-command', async ({ request }) => {
    sent.push((await request.json()) as Sent);
    return HttpResponse.json(refuse ? { status: 'FAILURE', message: 'nope' } : { status: 'SUCCESS', result: null });
  }));
  const { actions } = useMediaStore.getState();
  actions.ingest({ type: 'ma-players', players: [{ player_id: 'office', name: 'Office', available: true, state: 'playing', powered: true }] });
  actions.selectOutput('ma:office');
});

const office = () => useMediaStore.getState().players['ma:office'];

describe('usePlayback', () => {
  it('plays and queues on the selected output, with a toast for queueing', async () => {
    const { result } = renderHook(() => usePlayback(web));
    await act(() => result.current.play({ kind: 'uri', uri: 'library://track/1', title: 'One' }, 'row-1'));
    await act(() => result.current.enqueue({ kind: 'uri', uri: 'library://track/2', title: 'Two' }, 'next'));
    expect(sent.map((s) => s.args.option)).toEqual(['replace', 'next']);
    expect(result.current.busy).toBeNull();
  });

  it('shows a paused speaker at once after Pause', async () => {
    const { result } = renderHook(() => usePlayback(web));
    await act(() => result.current.transport('pause'));
    expect(office().status).toBe('paused');
  });

  it('reports a refused command as the page error', async () => {
    refuse = true;
    const { result } = renderHook(() => usePlayback(web));
    await act(() => result.current.transport('next'));
    expect(result.current.error).toBe('nope');
    act(() => result.current.clearError());
    expect(result.current.error).toBeNull();
    await act(() => result.current.play({ kind: 'uri', uri: 'x://1', title: 'X' }, 'k'));
    expect(result.current.error).toBe('nope');
    await act(() => result.current.enqueue({ kind: 'uri', uri: 'x://1', title: 'X' }, 'add'));
    expect(result.current.error).toBe('nope');
  });

  it('UX-38 sends only the settled volume, after showing every step', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = renderHook(() => usePlayback(web));
    act(() => {
      result.current.setVolume(10);
      result.current.setVolume(20);
      result.current.setVolume(30);
    });
    expect(office().volume).toBe(30);
    await act(async () => { await vi.advanceTimersByTimeAsync(VOLUME_DEBOUNCE_MS + 10); });
    await waitFor(() => expect(sent.filter((s) => s.command === 'players/cmd/volume_set')).toHaveLength(1));
    expect(sent.find((s) => s.command === 'players/cmd/volume_set')?.args.volume_level).toBe(30);
    vi.useRealTimers();
  });

  it('UX-53 rolls back mute, shuffle and repeat when the speaker refuses', async () => {
    refuse = true;
    useMediaStore.getState().actions.patchPlayer('ma:office', { shuffle: false, repeat: 'off' });
    const { result } = renderHook(() => usePlayback(web));
    act(() => result.current.setMuted(true));
    await waitFor(() => expect(office().muted).toBe(false));
    await act(() => result.current.setShuffle(true));
    expect(office().shuffle).toBe(false);
    await act(() => result.current.setRepeat('all'));
    expect(office().repeat).toBe('off');
  });

  it('moves the playhead before the seek is confirmed', async () => {
    const { result } = renderHook(() => usePlayback(web));
    await act(() => result.current.seek(90));
    expect(office().positionSec).toBe(90);
    expect(sent.at(-1)).toEqual({ command: 'player_queues/seek', args: { queue_id: 'office', position: 90 } });
  });

  it('drives this device without touching the store', async () => {
    useMediaStore.getState().actions.selectOutput('');
    const { result } = renderHook(() => usePlayback(web));
    act(() => result.current.setVolume(44));
    expect(web.setVolume).toHaveBeenCalledWith(44);
    await act(() => result.current.transport('resume'));
    expect(web.play).toHaveBeenCalledWith();
  });
});
