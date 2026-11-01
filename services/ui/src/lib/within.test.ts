import { describe, expect, it, vi } from 'vitest';
import { within } from './reportingAccess';

describe('within', () => {
  it('answers with the result when the check answers', async () => {
    await expect(within(Promise.resolve('yes'), 100, 'no')).resolves.toBe('yes');
  });

  it('answers with the fallback when the check fails', async () => {
    await expect(within(Promise.reject(new Error('x')), 100, 'no')).resolves.toBe('no');
  });

  it('answers with the fallback when the check never answers, so the report still goes out', async () => {
    vi.useFakeTimers();
    const pending = within(new Promise<string>(() => {}), 8000, 'no');
    vi.advanceTimersByTime(8000);
    await expect(pending).resolves.toBe('no');
    vi.useRealTimers();
  });
});
