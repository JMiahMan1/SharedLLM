/**
 * The side-by-side panes' own settings, remembered on this device.
 *
 * Each pane's type size is a number of A−/A+ steps away from the reader's own
 * size (`font_scale`, saved per user), so changing the reader's size still
 * moves both panes and a pane keeps its relative size. These are per device on
 * purpose: a phone's stacked panes and a desktop's columns want different
 * sizes. Storage that is missing or blocked simply means the defaults.
 */
import { TEXT_STEPS, nearestTextStep } from './readingPage';

export type PaneName = 'primary' | 'compare';

export interface ParallelPrefs {
  /** A−/A+ steps from the reader's own size, per pane. */
  steps: Record<PaneName, number>;
  /** Mark verses the two translations word differently. */
  showDiff: boolean;
}

export const PARALLEL_PREFS_KEY = 'jarvis_bible_parallel';

export const DEFAULT_PARALLEL_PREFS: ParallelPrefs = { steps: { primary: 0, compare: 0 }, showDiff: false };

const whole = (value: unknown): number => (Number.isInteger(value) ? (value as number) : 0);

export function readParallelPrefs(): ParallelPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(PARALLEL_PREFS_KEY) || '{}') as Partial<ParallelPrefs>;
    return {
      steps: { primary: whole(raw.steps?.primary), compare: whole(raw.steps?.compare) },
      showDiff: raw.showDiff === true,
    };
  } catch {
    return DEFAULT_PARALLEL_PREFS;
  }
}

export function writeParallelPrefs(prefs: ParallelPrefs): void {
  try {
    localStorage.setItem(PARALLEL_PREFS_KEY, JSON.stringify(prefs));
  } catch {
    // Private mode or full storage: the setting lasts this visit only.
  }
}

/** Where a pane sits on the size ladder: its scale and whether A− / A+ can still move. */
export function paneSize(base: number, steps: number): { scale: number; index: number; smallest: boolean; largest: boolean } {
  const index = Math.min(TEXT_STEPS.length - 1, Math.max(0, nearestTextStep(base) + steps));
  // At no offset the pane is exactly the reader's own size, even off the ladder.
  const scale = steps === 0 ? base : TEXT_STEPS[index];
  return { scale, index, smallest: index === 0, largest: index === TEXT_STEPS.length - 1 };
}

/** The new step offset after one A− (-1) or A+ (+1), clamped to the ladder. */
export function nudgeSteps(base: number, steps: number, direction: 1 | -1): number {
  const { index } = paneSize(base, steps);
  const next = Math.min(TEXT_STEPS.length - 1, Math.max(0, index + direction));
  return next - nearestTextStep(base);
}
