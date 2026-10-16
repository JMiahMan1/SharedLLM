import { describe, expect, it } from 'vitest';
import { formatRelativeTime, isUpcoming } from './eventTiming';

const now = new Date(2026, 9, 15, 20, 0); // Thu 15 Oct 2026, 8pm local

describe('formatRelativeTime', () => {
  it('counts days by calendar date, not 24-hour blocks', () => {
    // 13 hours away but tomorrow: used to read "In 13 hours".
    expect(formatRelativeTime(new Date(2026, 9, 16, 9, 0), undefined, now)).toBe('Tomorrow');
  });

  it('says "Now" for an event in progress instead of "Past"', () => {
    expect(formatRelativeTime(new Date(2026, 9, 15, 19, 0), new Date(2026, 9, 15, 21, 0), now)).toBe('Now');
  });

  it('gives minutes for something under an hour away', () => {
    expect(formatRelativeTime(new Date(2026, 9, 15, 20, 20), undefined, now)).toBe('In 20 min');
  });
});

describe('isUpcoming', () => {
  it('keeps a long event that started hours ago but has not ended', () => {
    const event = { start_time: new Date(2026, 9, 15, 8, 0).toISOString(), end_time: new Date(2026, 9, 15, 23, 0).toISOString() };
    expect(isUpcoming(event, now.getTime())).toBe(true);
  });

  it('drops an event that has ended', () => {
    const event = { start_time: new Date(2026, 9, 15, 19, 0).toISOString(), end_time: new Date(2026, 9, 15, 19, 30).toISOString() };
    expect(isUpcoming(event, now.getTime())).toBe(false);
  });
});
