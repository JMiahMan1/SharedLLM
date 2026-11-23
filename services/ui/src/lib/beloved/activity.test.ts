import { describe, expect, it } from 'vitest';
import type { BelovedActivityEntry } from './activity';
import { activityPhrase, hasRead, todayPhrases } from './activity';

const NOW = new Date(2026, 4, 12, 15, 30, 0);
const at = (day: number, hour = 12) => new Date(2026, 4, day, hour, 0, 0).getTime() / 1000;

const entry = (what: string, when: number, who = 'partnerB'): BelovedActivityEntry => ({ who, what, at: when });

describe('Beloved activity metadata', () => {
  it('uses only short content-free phrases, dropping unknown kinds', () => {
    expect(activityPhrase('gallery')).toBe('is looking through your photos');
    expect(activityPhrase('note')).toBe('read your love note');
    expect(activityPhrase('prayer')).toBe('read your prayer request');
    expect(activityPhrase('idea')).toBe('saw your date night idea');
    expect(activityPhrase('page')).toBe('opened Beloved');
    expect(activityPhrase('secret note text')).toBeUndefined();
  });

  it('shows only the spouse entries from today, newest first, capped', () => {
    const activity = [
      entry('gallery', at(12, 9)),
      entry('page', at(12, 14)),
      entry('prayer', at(12, 11)),
      entry('idea', at(11, 23)),
      entry('note', at(12, 8), 'partnerA'),
      entry('unknown-kind', at(12, 15)),
    ];
    expect(todayPhrases(activity, 'partnerA', 'partnerB', NOW)).toEqual([
      'opened Beloved',
      'read your prayer request',
      'is looking through your photos',
    ]);
    expect(todayPhrases(activity, 'partnerA', 'partnerB', NOW, 2)).toEqual([
      'opened Beloved',
      'read your prayer request',
    ]);
  });

  it('is empty when there is no spouse, when the spouse is the reader, or for my own signals', () => {
    const activity = [entry('page', at(12, 14)), entry('note', at(12, 8), 'partnerA')];
    expect(todayPhrases(activity, 'partnerA', '', NOW)).toEqual([]);
    expect(todayPhrases(activity, 'partnerA', 'partnerA', NOW)).toEqual([]);
    expect(todayPhrases([entry('note', at(12, 8), 'partnerA')], 'partnerA', 'partnerB', NOW)).toEqual([]);
  });

  it('marks a record read only after the spouse read that kind of record', () => {
    const activity = [
      entry('note', at(12, 11)),
      entry('prayer', at(12, 10), 'partnerA'),
    ];
    expect(hasRead(activity, 'note', at(12, 9), 'partnerB')).toBe(true);
    expect(hasRead(activity, 'note', at(12, 12), 'partnerB')).toBe(false);
    expect(hasRead(activity, 'idea', at(12, 9), 'partnerB')).toBe(false);
    expect(hasRead(activity, 'note', at(12, 9), 'partnerA')).toBe(false);
  });
});
