import { describe, expect, it } from 'vitest';
import { isIncomplete, keptChildren, rowFor, unlistedChildren } from './children';
import { specialDays } from './days';
import { buildView, readRecord, type FamilyChild } from './records';
import { moonOn, nightSky, wantsNightSky } from './sky';

const TODAY = { y: 2026, m: 11, d: 16 };
const FAMILY: FamilyChild[] = [
  { username: 'kid1', display_name: 'Robin', birth_date: '2016-12-01' },
  { username: 'kid2', display_name: 'Jamie', birth_date: null },
];

describe("the children's birthdays", () => {
  it('offers only the family children not listed yet, by account or by name', () => {
    expect(unlistedChildren(FAMILY, []).map((c) => c.username)).toEqual(['kid1', 'kid2']);
    expect(unlistedChildren(FAMILY, [{ name: '', birthday: '', username: 'kid1' }]).map((c) => c.username)).toEqual(['kid2']);
    expect(unlistedChildren(FAMILY, [{ name: ' jamie ', birthday: '' }]).map((c) => c.username)).toEqual(['kid1']);
  });

  it('fills in the birth date Jarvis has, and leaves it blank when it has none', () => {
    expect(rowFor(FAMILY[0])).toEqual({ name: 'Robin', birthday: '2016-12-01', username: 'kid1' });
    expect(rowFor(FAMILY[1])).toEqual({ name: 'Jamie', birthday: '', username: 'kid2' });
  });

  it('keeps complete rows only and says which ones are half done', () => {
    const rows = [
      { name: 'Robin', birthday: '2016-12-01', username: 'kid1' },
      { name: 'Jamie', birthday: '' },
      { name: '', birthday: '2020-02-29' },
      { name: '', birthday: '' },
    ];
    expect(keptChildren(rows)).toEqual([{ name: 'Robin', birthday: '2016-12-01', username: 'kid1' }]);
    expect(rows.map(isIncomplete)).toEqual([false, true, true, false]);
  });

  it('reads back only well-shaped children from a sealed profile', () => {
    const profile = readRecord('profile', {
      b: 'profile',
      children: [
        { name: 'Robin', birthday: '2016-12-01', username: 'kid1' },
        { name: 'Bad date', birthday: '2016-13-01' },
        { name: 'Odd user', birthday: '2018-05-05', username: 'not a username!' },
        'nonsense',
      ],
    });
    expect(profile).toEqual({
      b: 'profile',
      children: [
        { name: 'Robin', birthday: '2016-12-01', username: 'kid1' },
        { name: 'Odd user', birthday: '2018-05-05' },
      ],
    });
  });

  it('puts each child in the special days with the age they turn', () => {
    const view = buildView([
      { id: 'p', kind: 'profile', by: 'a', byName: 'A', own: true, at: 1, updatedAt: 1,
        record: { b: 'profile', children: [{ name: 'Robin', birthday: '2016-12-01' }, { name: 'Baby', birthday: '2027-03-01' }] } },
    ]);
    const days = specialDays(view, [], TODAY);
    const robin = days.find((d) => d.name.startsWith('Robin'));
    expect(robin).toMatchObject({ name: "Robin's 10th birthday", kind: 'child-birthday', inDays: 15 });
    // A birthday still to come for the first time is a due date, not an age.
    expect(days.find((d) => d.name.startsWith('Baby'))?.name).toBe("Baby's birthday");
  });
});

describe('the night sky', () => {
  it('knows the phase of the moon', () => {
    // Full moon 2025-11-05 13:19 UTC, new moon 2025-11-20 06:47 UTC.
    expect(moonOn({ y: 2025, m: 11, d: 5 }).name).toBe('Full moon');
    expect(moonOn({ y: 2025, m: 11, d: 5 }).lit).toBeGreaterThan(0.97);
    expect(moonOn({ y: 2025, m: 11, d: 20 }).name).toBe('New moon');
    expect(moonOn({ y: 2025, m: 11, d: 20 }).lit).toBeLessThan(0.03);
  });

  it('calls the night from its cloud cover and the moon', () => {
    const newMoon = { y: 2025, m: 11, d: 20 };
    const fullMoon = { y: 2025, m: 11, d: 5 };
    expect(nightSky(newMoon, 10).look).toBe('clear');
    expect(nightSky(fullMoon, 10).look).toBe('bright-moon');
    expect(nightSky(newMoon, 45).look).toBe('some-cloud');
    expect(nightSky(newMoon, 90)).toMatchObject({ look: 'cloudy', cloud: 90, verdict: 'Cloudy, the stars will be hidden' });
    // Past the forecast: only the moon.
    expect(nightSky(newMoon, null)).toMatchObject({ cloud: null, look: null, verdict: null });
    expect(nightSky(newMoon, undefined).moon.name).toBe('New moon');
  });

  it('shows the night for evenings and stargazing, not for a morning plan', () => {
    expect(wantsNightSky(undefined, undefined)).toBe(true);
    expect(wantsNightSky('20:30', undefined)).toBe(true);
    expect(wantsNightSky('09:00', undefined)).toBe(false);
    expect(wantsNightSky('09:00', 'stars')).toBe(true);
  });
});
