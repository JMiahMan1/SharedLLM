import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useTalkLongPoll } from './useTalkLongPoll';
import { api } from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return { ...actual, api: { ...(actual.api as Record<string, unknown>), waitTalkMessages: vi.fn() } };
});

const never = () => new Promise<never>(() => {});

describe('useTalkLongPoll', () => {
  beforeEach(() => {
    vi.mocked(api.waitTalkMessages).mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it('waits after the newest message and reports news the moment it lands', async () => {
    vi.mocked(api.waitTalkMessages)
      .mockResolvedValueOnce({ status: 'SUCCESS', message: '1 new message(s).', service: 'talk_wait', detail: { messages: [{ id: 11 }] } })
      .mockImplementation(never);
    const onNews = vi.fn();

    const { result } = renderHook(() => useTalkLongPoll('room-1', 10, onNews));

    await waitFor(() => expect(onNews).toHaveBeenCalledTimes(1));
    expect(result.current).toBe(true);
    expect(vi.mocked(api.waitTalkMessages).mock.calls[0].slice(0, 3)).toEqual(['room-1', 10, 25]);
    // The next wait starts after the message it just heard about.
    await waitFor(() => expect(vi.mocked(api.waitTalkMessages).mock.calls[1]?.[1]).toBe(11));
  });

  it('does not poll until it knows the newest message', async () => {
    renderHook(() => useTalkLongPoll('room-1', 0, vi.fn()));
    await new Promise((r) => setTimeout(r, 50));
    expect(api.waitTalkMessages).not.toHaveBeenCalled();
  });

  it('backs off after a failure instead of spinning', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.waitTalkMessages).mockRejectedValue(new Error('down'));

    const { result } = renderHook(() => useTalkLongPoll('room-1', 10, vi.fn()));

    await waitFor(() => expect(api.waitTalkMessages).toHaveBeenCalledTimes(1));
    expect(result.current).toBe(false);
    await vi.advanceTimersByTimeAsync(2000);
    expect(api.waitTalkMessages).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1500);
    expect(api.waitTalkMessages).toHaveBeenCalledTimes(2);
  });

  it('stops when the conversation closes', async () => {
    vi.mocked(api.waitTalkMessages).mockImplementation(never);
    const { unmount } = renderHook(() => useTalkLongPoll('room-1', 10, vi.fn()));
    await waitFor(() => expect(api.waitTalkMessages).toHaveBeenCalledTimes(1));
    const signal = vi.mocked(api.waitTalkMessages).mock.calls[0][4] as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);
  });
});
