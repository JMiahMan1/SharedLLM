import { describe, expect, it } from 'vitest';
import { entriesNeedingAttention } from './discoveredDevices';

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
