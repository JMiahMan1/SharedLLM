import { describe, expect, it, vi } from 'vitest';
import {
  DEFAULT_PARALLEL_PREFS,
  PARALLEL_PREFS_KEY,
  nudgeSteps,
  paneSize,
  readParallelPrefs,
  writeParallelPrefs,
} from './parallelPrefs';

// The test setup clears storage before every test.
describe('parallelPrefs', () => {
  it('reads the defaults when nothing or nonsense is stored', () => {
    expect(readParallelPrefs()).toEqual(DEFAULT_PARALLEL_PREFS);
    localStorage.setItem(PARALLEL_PREFS_KEY, '{not json');
    expect(readParallelPrefs()).toEqual(DEFAULT_PARALLEL_PREFS);
    localStorage.setItem(PARALLEL_PREFS_KEY, JSON.stringify({ steps: { primary: 'big', compare: 1.5 }, showDiff: 'yes' }));
    expect(readParallelPrefs()).toEqual(DEFAULT_PARALLEL_PREFS);
  });

  it('round-trips what was saved', () => {
    writeParallelPrefs({ steps: { primary: -1, compare: 2 }, showDiff: true });
    expect(readParallelPrefs()).toEqual({ steps: { primary: -1, compare: 2 }, showDiff: true });
  });

  it('keeps working when storage refuses to write', () => {
    const spy = vi.spyOn(localStorage, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    expect(() => writeParallelPrefs({ steps: { primary: 1, compare: 0 }, showDiff: false })).not.toThrow();
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });

  it('steps a pane from the reader’s own size and stops at the ends of the ladder', () => {
    expect(paneSize(1, 0)).toMatchObject({ scale: 1, smallest: false, largest: false });
    expect(paneSize(1, 1).scale).toBe(1.15);
    expect(paneSize(1, -1)).toMatchObject({ scale: 0.85, smallest: true });
    expect(paneSize(1, 9)).toMatchObject({ scale: 1.5, largest: true });
    // A reader size off the ladder is kept as is until the pane is nudged.
    expect(paneSize(1.1, 0).scale).toBe(1.1);
    expect(paneSize(1.1, 1).scale).toBe(1.3);
  });

  it('never stores an offset past the end of the ladder', () => {
    expect(nudgeSteps(1, 0, 1)).toBe(1);
    expect(nudgeSteps(1, -1, -1)).toBe(-1);
    expect(nudgeSteps(1.5, 0, 1)).toBe(0);
    expect(nudgeSteps(1, 9, -1)).toBe(2);
  });
});
