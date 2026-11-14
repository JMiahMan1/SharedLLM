import { describe, expect, it, vi, beforeEach } from 'vitest';
import { STALE_AFTER_S, getDeployStatus, nextPhase } from './deployStatus';
import { apiClient } from '../services/api';

vi.mock('../services/api', () => ({ apiClient: { get: vi.fn() } }));

const NOW = Date.parse('2026-11-14T12:00:00Z');
const at = (agoS: number) => NOW / 1000 - agoS;

describe('nextPhase', () => {
  it('shows updating while a fresh flag is up, and ignores one a dead deploy left behind', () => {
    expect(nextPhase('none', { updating: true, at: at(20), app: false }, NOW, false)).toBe('updating');
    expect(nextPhase('none', { updating: true, at: at(STALE_AFTER_S + 1), app: true }, NOW, false)).toBe('none');
  });

  it('offers a browser a reload once an update that changed the app is done', () => {
    expect(nextPhase('updating', { updating: false, at: at(1), app: true }, NOW, false)).toBe('updated');
    expect(nextPhase('updated', { updating: false, at: at(60), app: true }, NOW, false)).toBe('updated');
  });

  it('just goes away when the app did not change, on the phone app, or when it never saw the update', () => {
    expect(nextPhase('updating', { updating: false, at: at(1), app: false }, NOW, false)).toBe('none');
    expect(nextPhase('updating', { updating: false, at: at(1), app: true }, NOW, true)).toBe('none');
    expect(nextPhase('none', { updating: false, at: at(1), app: true }, NOW, false)).toBe('none');
    expect(nextPhase('none', null, NOW, false)).toBe('none');
  });
});

describe('getDeployStatus', () => {
  beforeEach(() => vi.mocked(apiClient.get).mockReset());

  it('reads the flag quietly', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { updating: true, at: 1, app: true } });
    expect(await getDeployStatus()).toEqual({ updating: true, at: 1, app: true });
    expect(vi.mocked(apiClient.get).mock.calls[0][1]).toMatchObject({ silent: true });
  });

  it('is null when there is no flag or it is not one', async () => {
    vi.mocked(apiClient.get).mockRejectedValue(new Error('404'));
    expect(await getDeployStatus()).toBeNull();
    vi.mocked(apiClient.get).mockResolvedValue({ data: '<html>' });
    expect(await getDeployStatus()).toBeNull();
  });
});
