import { describe, it, expect, vi, beforeEach } from 'vitest';
import { checkGuard } from './guardWatch';
import Guard from '../plugins/guard';
import { api } from '../services/api';
import { storageSet } from './storage';

vi.mock('../plugins/guard', () => ({ default: { status: vi.fn() } }));
vi.mock('../services/api', () => ({ api: { getScreenTimeStatus: vi.fn(), reportGuardTamper: vi.fn() } }));

const guard = (guardEnabled: boolean) => ({ adminActive: true, deviceOwner: false, guardEnabled });

describe('checkGuard', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    await storageSet('jarvis_guard_seen_on', '');
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue({ protected: true } as never);
  });

  it('stays quiet before setup was ever finished', async () => {
    vi.mocked(Guard.status).mockResolvedValue(guard(false));
    await checkGuard('kid');
    expect(api.reportGuardTamper).not.toHaveBeenCalled();
  });

  it('tells the parents when a guard that was on is off now', async () => {
    vi.mocked(Guard.status).mockResolvedValueOnce(guard(true)).mockResolvedValueOnce(guard(false));
    await checkGuard('kid');
    await checkGuard('kid');
    expect(api.reportGuardTamper).toHaveBeenCalledWith('guard_missing');
  });

  it('never reports for a phone that is not protected', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue({ protected: false } as never);
    vi.mocked(Guard.status).mockResolvedValueOnce(guard(true)).mockResolvedValueOnce(guard(false));
    await checkGuard('dad');
    await checkGuard('dad');
    expect(api.reportGuardTamper).not.toHaveBeenCalled();
  });
});
