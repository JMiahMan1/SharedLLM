import { beforeEach, describe, expect, it, vi } from 'vitest';

const geo = vi.hoisted(() => ({
  checkPermissions: vi.fn(),
  requestPermissions: vi.fn(),
}));
vi.mock('@capacitor/geolocation', () => ({ Geolocation: geo }));

import { accessFrom, ensureLocationAccess, highAccuracy, resetLocationAccessForTests } from './locationPermission';

const status = (location: string, coarseLocation: string) => ({ location, coarseLocation }) as never;

describe('location permission', () => {
  beforeEach(() => {
    geo.checkPermissions.mockReset();
    geo.requestPermissions.mockReset();
    resetLocationAccessForTests();
  });

  it('reads precise, approximate, denied and unanswered', () => {
    expect(accessFrom(status('granted', 'granted'))).toBe('precise');
    expect(accessFrom(status('denied', 'granted'))).toBe('approximate');
    expect(accessFrom(status('denied', 'denied'))).toBe('denied');
    expect(accessFrom(status('prompt', 'prompt'))).toBeNull();
    expect(accessFrom(status('prompt-with-rationale', 'prompt-with-rationale'))).toBeNull();
  });

  it('asks once for callers that arrive together', async () => {
    geo.checkPermissions.mockResolvedValue(status('prompt', 'prompt'));
    let answer: (v: unknown) => void = () => {};
    geo.requestPermissions.mockReturnValue(new Promise((r) => { answer = r; }));
    const calls = [ensureLocationAccess(), ensureLocationAccess(), ensureLocationAccess()];
    await Promise.resolve();
    await Promise.resolve();
    answer(status('granted', 'granted'));
    expect(await Promise.all(calls)).toEqual(['precise', 'precise', 'precise']);
    expect(geo.requestPermissions).toHaveBeenCalledTimes(1);
  });

  it('does not ask again once answered, and keeps an approximate grant', async () => {
    geo.checkPermissions.mockResolvedValue(status('denied', 'granted'));
    const access = await ensureLocationAccess();
    expect(access).toBe('approximate');
    expect(highAccuracy(access)).toBe(false);
    expect(geo.requestPermissions).not.toHaveBeenCalled();
  });

  it('asks again when the state reads denied, so turning location back on can work', async () => {
    geo.checkPermissions.mockResolvedValue(status('denied', 'denied'));
    geo.requestPermissions.mockResolvedValue(status('granted', 'granted'));
    expect(await ensureLocationAccess()).toBe('precise');
    expect(geo.requestPermissions).toHaveBeenCalledTimes(1);
  });

  it('never shows a dialog when told not to ask', async () => {
    geo.checkPermissions.mockResolvedValue(status('prompt', 'prompt'));
    expect(await ensureLocationAccess({ ask: false })).toBe('denied');
    expect(geo.requestPermissions).not.toHaveBeenCalled();
  });
});
