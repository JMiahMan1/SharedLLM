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
    expect(line).toContain('6h left of 16h');
  });

  it('says the goal is reached instead of a time left', () => {
    expect(sharedFastingLine({ active: fast({ reached: true, remaining_minutes: 0 }), last: null }, now)).toContain(
      '16h goal reached',
    );
  });

  it('shows the last fast when none is running', () => {
    const last = fast({ ended_at: new Date(2026, 10, 1, 12, 30).toISOString(), elapsed_minutes: 990 });
    expect(sharedFastingLine({ active: null, last }, now)).toMatch(/^Not fasting · last fast 16h 30m, ended 12:30/);
    expect(sharedFastingLine({ active: null, last: null }, now)).toBe('Not fasting');
    expect(sharedFastingLine(undefined, now)).toBeNull();
  });
});
