import { describe, expect, it } from 'vitest';
import { entriesNeedingAttention, offlineSince } from './discoveredDevices';

const entry = (title: string, state: string, disabled_by: string | null = null) => ({ title, state, disabled_by });

describe('entriesNeedingAttention', () => {
  it('lists connections that are not working, failures first, and skips ones switched off on purpose', () => {
    const out = entriesNeedingAttention([
      entry('Music', 'setup_retry'), entry('Lights', 'loaded'), entry('Cameras', 'setup_error'),
      entry('Old bridge', 'not_loaded', 'user'), entry('Speakers', 'not_loaded'),
    ]);
    expect(out.map((e) => e.title)).toEqual(['Cameras', 'Speakers', 'Music']);
  });

  it('is empty when everything works', () => {
    expect(entriesNeedingAttention([entry('A', 'loaded')])).toEqual([]);
  });
});

describe('offlineSince', () => {
  const now = Date.parse('2026-11-14T12:00:00Z');
  const before = (ms: number) => new Date(now - ms).toISOString();
  it('says how long in the largest whole unit', () => {
    expect(offlineSince(before(30_000), now)).toBe('Not answering since just now');
    expect(offlineSince(before(45 * 60_000), now)).toBe('Not answering for 45 minutes');
    expect(offlineSince(before(60 * 60_000 * 1.5), now)).toBe('Not answering for 2 hours');
    expect(offlineSince(before(86_400_000 * 2), now)).toBe('Not answering for 2 days');
    expect(offlineSince(before(86_400_000 * 1.6), now)).toBe('Not answering for 2 days');
  });
  it('is empty when the time is not known', () => {
    expect(offlineSince(null, now)).toBe('');
    expect(offlineSince('not a date', now)).toBe('');
  });
});
