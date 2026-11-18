import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import * as setup from '../lib/homeSetup';
import { confirmDialog } from '../components/ui/confirmDialog';
import { AREAS_KEY, FLOORS_KEY, REMOVE_ROOM_BODY, ROOMS_KEY, useRoomActions } from './useRoomActions';

vi.mock('../lib/homeSetup', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return { ...real, createArea: vi.fn(), updateArea: vi.fn(), deleteArea: vi.fn() };
});
vi.mock('../components/ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));
vi.mock('react-hot-toast', () => {
  const t = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: t };
});

const m = vi.mocked(setup);
const ask = vi.mocked(confirmDialog);
const STUDY = { area_id: 'study', name: 'Study', floor_id: null, icon: null, aliases: [], labels: [] };

function setupHook() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const { result } = renderHook(() => useRoomActions(), { wrapper });
  const refreshed = () => invalidate.mock.calls.map(([f]) => (f as { queryKey: unknown }).queryKey);
  return { result, refreshed };
}

beforeEach(() => vi.clearAllMocks());

describe('useRoomActions', () => {
  it('adds a room with a trimmed name, says so, and refreshes every list of rooms', async () => {
    m.createArea.mockResolvedValue(STUDY);
    const { result, refreshed } = setupHook();
    act(() => result.current.add.mutate({ name: '  Study ', floor_id: '' }));
    await waitFor(() => expect(result.current.add.isSuccess).toBe(true));
    expect(m.createArea).toHaveBeenCalledWith({ name: 'Study', floor_id: null });
    expect(toast.success).toHaveBeenCalledWith('Study was added.');
    expect(refreshed()).toEqual(expect.arrayContaining([AREAS_KEY, FLOORS_KEY, ROOMS_KEY, ['homehub-hardware']]));
  });

  it('renames or moves a room and refreshes', async () => {
    m.updateArea.mockResolvedValue({ ...STUDY, name: 'Library' });
    const { result, refreshed } = setupHook();
    act(() => result.current.update.mutate({ id: 'study', body: { name: 'Library' } }));
    await waitFor(() => expect(result.current.update.isSuccess).toBe(true));
    expect(m.updateArea).toHaveBeenCalledWith('study', { name: 'Library' });
    expect(refreshed()).toEqual(expect.arrayContaining([ROOMS_KEY]));
  });

  it('asks before removing, says what happens to the devices, and removes on yes', async () => {
    m.deleteArea.mockResolvedValue({ devices: 2, entities: 3 });
    const { result } = setupHook();
    let said = false;
    await act(async () => { said = await result.current.confirmRemove(STUDY); });
    expect(said).toBe(true);
    expect(ask).toHaveBeenCalledWith(expect.objectContaining({ title: 'Remove Study?', body: REMOVE_ROOM_BODY, danger: true }));
    expect(REMOVE_ROOM_BODY).toMatch(/devices keep working/);
    await waitFor(() => expect(m.deleteArea).toHaveBeenCalledWith('study'));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Study was removed.'));
  });

  it('keeps the room when the person says no', async () => {
    ask.mockResolvedValueOnce(false);
    const { result } = setupHook();
    await act(async () => { await result.current.confirmRemove(STUDY); });
    expect(m.deleteArea).not.toHaveBeenCalled();
  });

  it('shows the hub refusal (a member is not allowed) as an error', async () => {
    m.createArea.mockRejectedValue(new Error('Only a parent can change rooms'));
    const { result } = setupHook();
    act(() => result.current.add.mutate({ name: 'Fort' }));
    await waitFor(() => expect(result.current.add.isError).toBe(true));
    expect(toast.error).toHaveBeenCalled();
  });
});
