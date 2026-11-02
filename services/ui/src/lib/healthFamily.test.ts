import { describe, expect, it } from 'vitest';
import { sharedFastingLine } from './healthFamily';
import type { HealthFast } from '../types/api';

const now = new Date(2026, 10, 1, 18, 0);
const fast = (overrides: Partial<HealthFast>): HealthFast => ({
  id: 1,
  started_at: new Date(2026, 10, 1, 8, 0).toISOString(),
  ended_at: null,
  target_hours: 16,
  elapsed_minutes: 600,
  target_minutes: 960,
  percent: 63,
  remaining_minutes: 360,
  reached: false,
  ...overrides,
});

describe('sharedFastingLine', () => {
  it('names when the fast started, how long so far and the time left', () => {
    const line = sharedFastingLine({ active: fast({}), last: null }, now);
    expect(line).toMatch(/^Fasting since 8:00/);
    expect(line).toContain('10h so far');
    expect(line).toContain('6h left');
  });

  it('counts from the start and end times, not the numbers when it was read', () => {
    // The server's numbers are from an hour ago; the line is for now.
    const line = sharedFastingLine({ active: fast({ remaining_minutes: 420 }), last: null }, now);
    expect(line).toContain('6h left');
    const later = new Date(2026, 10, 2, 1, 0);
    expect(sharedFastingLine({ active: fast({}), last: null }, later)).toContain('complete');
  });

  it('names the kind of fast, its day in a season, and what it is for', () => {
    const lent = fast({
      kind: 'lent', kind_name: 'Great Lent (the Forty Days)', target_hours: null, days: 40,
      ends_at: new Date(2026, 11, 11, 8, 0).toISOString(), intention: 'our neighbors',
      started_at: new Date(2026, 9, 30, 8, 0).toISOString(),
    });
    const line = sharedFastingLine({ active: lent, last: null }, now);
    expect(line).toMatch(/^Great Lent \(the Forty Days\) since/);
    expect(line).toContain('day 3 of 40');
    expect(line).toContain('praying for our neighbors');
  });

  it('shows the last fast when none is running', () => {
    const last = fast({ ended_at: new Date(2026, 10, 1, 12, 30).toISOString(), elapsed_minutes: 990 });
    expect(sharedFastingLine({ active: null, last }, now)).toMatch(/^Not fasting · last fast 16h 30m, ended 12:30/);
    expect(sharedFastingLine({ active: null, last: null }, now)).toBe('Not fasting');
    expect(sharedFastingLine(undefined, now)).toBeNull();
  });
});
