import { describe, it, expect, vi, afterEach } from 'vitest';
import { holdScreenOn } from './keepAwake';

afterEach(() => {
  delete (navigator as unknown as { wakeLock?: unknown }).wakeLock;
});

describe('holdScreenOn', () => {
  it("uses the browser's screen wake lock when there is no KeepAwake plugin", async () => {
    const release = vi.fn(async () => undefined);
    const request = vi.fn(async () => ({ release }));
    Object.defineProperty(navigator, 'wakeLock', { value: { request }, configurable: true });
    const hold = await holdScreenOn();
    expect(request).toHaveBeenCalledWith('screen');
    expect(hold?.how).toBe('wake-lock');
    await hold?.release();
    expect(release).toHaveBeenCalled();
  });

  it('answers null (so the page can say so) when nothing can hold the screen on', async () => {
    expect(await holdScreenOn()).toBeNull();
    Object.defineProperty(navigator, 'wakeLock', { value: { request: vi.fn(async () => { throw new Error('denied'); }) }, configurable: true });
    expect(await holdScreenOn()).toBeNull();
  });
});
