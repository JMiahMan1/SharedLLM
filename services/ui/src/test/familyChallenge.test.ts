import { describe, expect, it } from 'vitest';
import { challengeSharePreview, challengeShareText, standingLabel, standingText } from '../lib/familyChallenge';
import type { HealthChallenge } from '../types/api';

const challenge = (overrides: Partial<HealthChallenge> = {}): HealthChallenge => ({
  id: 1,
  kind: 'water_days',
  title: 'Water goal days',
  unit: 'days',
  detail: '',
  week_start: '2026-10-19',
  week_end: '2026-10-25',
  target_days: 5,
  created_by: 'dad',
  joined: true,
  leaderboard: [
    { user_id: 'kid', count: 5, days: [], reached: true, guideline_goal: true },
    { user_id: 'dad', count: 2, days: [], reached: false, guideline_goal: false },
  ],
  ...overrides,
});

describe('familyChallenge', () => {
  it('words a standing', () => {
    expect(standingText(2, 5, 'nights')).toBe('2 of 5 nights');
  });

  it('shares the ranked standings with names and an intro', () => {
    const text = challengeShareText(challenge(), (id) => (id === 'kid' ? 'Sam' : id), 'Go team');
    const lines = text.split('\n');
    expect(lines[0]).toBe('Go team');
    expect(lines[2]).toMatch(/^🏆 Family challenge: Water goal days - 5 days this week/);
    expect(lines[3]).toBe('1. Sam · 5 of 5 days ✓');
    expect(lines[4]).toBe('2. dad · 2 of 5 days');
    expect(lines[5]).toBe('Join it on Health → Today.');
  });

  it('says a standing is not known rather than zero when steps could not be read', () => {
    const row = { user_id: 'kid', count: 0, days: [], reached: false, guideline_goal: false, unavailable: true };
    expect(standingLabel(row, 4, 'days')).toBe('not available right now');
    expect(standingLabel({ ...row, unavailable: false }, 4, 'days')).toBe('0 of 4 days');
    const steps = challenge({ kind: 'step_days', title: 'Step goal days', target_days: 4, leaderboard: [row] });
    expect(challengeShareText(steps)).toContain('1. kid · not available right now');
  });

  it('says when nobody has joined', () => {
    expect(challengeShareText(challenge({ leaderboard: [] }))).toContain('Nobody has joined yet.');
  });

  it('previews the challenge in one line', () => {
    expect(challengeSharePreview(challenge())).toBe('Water goal days: 5 days this week · 2 joined');
  });
});
