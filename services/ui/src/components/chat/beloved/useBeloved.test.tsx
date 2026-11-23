import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_BELOVED_PREFS } from '../../../lib/beloved/activity';
import { useBeloved } from './useBeloved';

const getBeloved = vi.fn(async () => ({
  me: 'partnerA',
  couple: [
    { username: 'partnerA', display_name: 'Alex', me: true },
    { username: 'partnerB', display_name: 'Robin', me: false },
  ],
  records: [],
}));
const belovedActivity = vi.fn(async () => ({ me: 'partnerA', activity: [] }));
const getBelovedPrefs = vi.fn(async () => ({ ...DEFAULT_BELOVED_PREFS }));
const setBelovedPrefs = vi.fn(async (prefs: Record<string, unknown>) => ({ ...DEFAULT_BELOVED_PREFS, ...prefs }));
const belovedSeen = vi.fn(async () => ({ shared: true, notified: false }));
const addBelovedRecord = vi.fn(async () => ({ id: 'new', notified: true }));
const updateBelovedRecord = vi.fn(async () => ({ id: 'old', notified: true }));
const deleteBelovedRecord = vi.fn(async () => undefined);

vi.mock('../../../services/api', () => ({
  api: {
    getBeloved: (...a: Parameters<typeof getBeloved>) => getBeloved(...a),
    belovedActivity: (...a: Parameters<typeof belovedActivity>) => belovedActivity(...a),
    getBelovedPrefs: (...a: Parameters<typeof getBelovedPrefs>) => getBelovedPrefs(...a),
    setBelovedPrefs: (...a: Parameters<typeof setBelovedPrefs>) => setBelovedPrefs(...a),
    belovedSeen: (...a: Parameters<typeof belovedSeen>) => belovedSeen(...a),
    addBelovedRecord: (...a: Parameters<typeof addBelovedRecord>) => addBelovedRecord(...a),
    updateBelovedRecord: (...a: Parameters<typeof updateBelovedRecord>) => updateBelovedRecord(...a),
    deleteBelovedRecord: (...a: Parameters<typeof deleteBelovedRecord>) => deleteBelovedRecord(...a),
  },
}));

const vault = { unlocked: true, sealRecord: vi.fn(async () => 'sealed'), openRecord: vi.fn(async () => null) };

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  for (const fn of [getBeloved, belovedActivity, getBelovedPrefs, setBelovedPrefs, belovedSeen, addBelovedRecord, updateBelovedRecord, deleteBelovedRecord]) {
    fn.mockClear();
  }
  getBelovedPrefs.mockResolvedValue({ ...DEFAULT_BELOVED_PREFS });
});

describe('useBeloved metadata', () => {
  it('exposes the spouse and loads reading preferences', async () => {
    const { result } = renderHook(() => useBeloved('room one', vault as never), { wrapper });
    await waitFor(() => expect(result.current.prefs).toEqual(DEFAULT_BELOVED_PREFS));
    await waitFor(() => expect(result.current.spouse?.display_name).toBe('Robin'));
    await waitFor(() => expect(belovedSeen).toHaveBeenCalledWith('room one', 'page'));
  });

  it('writes preference changes straight to the server', async () => {
    const { result } = renderHook(() => useBeloved('room one', vault as never), { wrapper });
    await waitFor(() => expect(result.current.prefs).toEqual(DEFAULT_BELOVED_PREFS));
    await act(async () => {
      await result.current.setPrefs({ share_activity: false, encourage_hour: 8 });
    });
    expect(setBelovedPrefs).toHaveBeenCalledWith('room one', { share_activity: false, encourage_hour: 8 });
  });

  it('sends a heart with notify, and passes notify through on record edits', async () => {
    const { result } = renderHook(() => useBeloved('room one', vault as never), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      await result.current.sendHeart();
    });
    expect(addBelovedRecord).toHaveBeenCalledWith('room one', 'heart', 'sealed', true);

    await act(async () => {
      await result.current.save('note', { b: 'note', text: 'hi' } as never, { id: 'rec-1', notify: false });
    });
    expect(updateBelovedRecord).toHaveBeenCalledWith('room one', 'note', 'rec-1', 'sealed', false);
    await act(async () => {
      await result.current.save('prayer', { b: 'prayer', text: 'hi' } as never, { id: 'rec-2', notify: true });
    });
    expect(updateBelovedRecord).toHaveBeenLastCalledWith('room one', 'prayer', 'rec-2', 'sealed', true);
  });
});
