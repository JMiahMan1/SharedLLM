import { beforeEach, describe, expect, it, vi } from 'vitest';
import { listRemoteMaPlayers } from './maRemotePlayer';
import { api } from '../services/api';

vi.mock('../services/api', () => ({
  api: { mediaMaCommand: vi.fn() },
}));

/**
 * A failed `players/all` read is unknown, not an empty list. Returning [] used
 * to wipe the output picker whenever Music Assistant was briefly unreachable.
 */
describe('listRemoteMaPlayers', () => {
  beforeEach(() => vi.clearAllMocks());

  it('returns null (unknown) when the read fails, so the caller keeps its list', async () => {
    vi.mocked(api.mediaMaCommand).mockRejectedValueOnce(new Error('unreachable'));
    await expect(listRemoteMaPlayers()).resolves.toBeNull();
  });

  it('returns null when MA answers with a failure status', async () => {
    vi.mocked(api.mediaMaCommand).mockResolvedValueOnce({ status: 'FAILURE', message: 'nope' } as never);
    await expect(listRemoteMaPlayers()).resolves.toBeNull();
  });

  it('returns the parsed list when MA answers', async () => {
    vi.mocked(api.mediaMaCommand).mockResolvedValueOnce({
      status: 'SUCCESS',
      result: [{ player_id: 'office', name: 'Office', available: true, state: 'playing', powered: true }],
    } as never);
    await expect(listRemoteMaPlayers()).resolves.toEqual([
      { player_id: 'office', name: 'Office', available: true, state: 'playing', powered: true },
    ]);
  });
});
