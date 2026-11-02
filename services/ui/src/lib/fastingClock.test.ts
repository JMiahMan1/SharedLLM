import { describe, expect, it } from 'vitest';
import { startedAtFromClock } from './fastingClock';

describe('startedAtFromClock', () => {
  const now = new Date(2026, 10, 1, 19, 29, 27); // Sunday 7:29:27 PM, this device's time

  it('never moves a time to another day: one not yet come is now', () => {
    // Typing 7:30 at 7:29 once filed a Sunday fast on Saturday.
    expect(startedAtFromClock('19:30', now)).toBe(now.toISOString());
    expect(startedAtFromClock('21:00', now)).toBe(now.toISOString());
  });

  it('reads an earlier time as today', () => {
    expect(startedAtFromClock('18:00', now)).toBe(new Date(2026, 10, 1, 18, 0).toISOString());
  });
});
