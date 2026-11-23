import { describe, expect, it } from 'vitest';
import { dayNumber, localToday, type Ymd } from './dates';
import { BELOVED_TIPS, dailyTip } from './tips';

describe('daily Beloved tip', () => {
  it('is deterministic for a day and rotates to the next tip the next day', () => {
    const today: Ymd = { y: 2026, m: 4, d: 12 };
    const first = dailyTip(today);
    expect(first).toBe(dailyTip(today));
    expect(first).toBe(BELOVED_TIPS[((dayNumber(today) % BELOVED_TIPS.length) + BELOVED_TIPS.length) % BELOVED_TIPS.length]);
    const tomorrow = localToday(new Date(2026, 4, 13, 0, 0, 0));
    expect(dailyTip(tomorrow)).toBe(BELOVED_TIPS[(BELOVED_TIPS.indexOf(first) + 1) % BELOVED_TIPS.length]);
  });

  it('visits every tip in the rotation over ten consecutive days', () => {
    const seen = new Set<string>();
    for (let i = 0; i < BELOVED_TIPS.length; i += 1) {
      const day = localToday(new Date(2026, 0, 1 + i, 12, 0, 0));
      const tip = dailyTip(day);
      expect(tip.text).toMatch(/^Did you know\? /);
      expect(tip.link.length).toBeGreaterThan(0);
      expect(tip.target).toBe(tip.id);
      seen.add(tip.id);
    }
    expect(seen.size).toBe(BELOVED_TIPS.length);
  });

  it('covers all ten features named for the page', () => {
    expect(BELOVED_TIPS.map((tip) => tip.id)).toEqual([
      'plan', 'prayer', 'idea', 'note', 'question', 'day', 'children', 'cover', 'devotion', 'heart',
    ]);
  });
});
