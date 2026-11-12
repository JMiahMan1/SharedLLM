import { describe, it, expect } from 'vitest';
import { buildFamilyCards, groupByPlace, placesAt, type FamilyMemberStatus, type Place } from './familyCards';

const HOME: Place = { name: 'Home', lat: 39.4566, lon: -104.4848, radius: 100 };
const WORK: Place = { name: 'Work', lat: 39.59, lon: -104.82, radius: 150 };
const NOW = Date.parse('2026-10-15T22:00:00Z');
const sec = (iso: string) => Date.parse(iso) / 1000;

const person = (id: string, state: string, lastUpdated: string, extra: Record<string, unknown> = {}) => ({
  properties: { entity_id: `person.${id}`, friendly_name: id[0].toUpperCase() + id.slice(1), state, last_updated: lastUpdated, battery: 80, ...extra },
});

describe('buildFamilyCards', () => {
  it("takes the app's newer fix over a HA person that stopped following the phone", () => {
    const cards = buildFamilyCards(
      [person('alex', 'home', '2026-10-14T18:00:00Z')],
      { alex: { latitude: 39.5901, longitude: -104.8201, speed: 0, accuracy: 18, timestamp: sec('2026-10-15T21:59:30Z') } },
      [HOME, WORK],
      NOW,
    );
    expect(cards).toHaveLength(1);
    expect(cards[0]).toMatchObject({ id: 'person.alex', zone: 'Work', source: 'app', accuracy: 18, battery: 80 });
    expect(cards[0].lastUpdated).toBe('2026-10-15T21:59:30.000Z');
  });

  it("shows the phone's charging state when the app reports it", () => {
    const [charging, older] = buildFamilyCards(
      [],
      {
        alex: { latitude: HOME.lat, longitude: HOME.lon, battery: 40, charging: true, timestamp: sec('2026-10-15T21:59:30Z') },
        sam: { latitude: HOME.lat, longitude: HOME.lon, battery: 40, timestamp: sec('2026-10-15T21:59:30Z') },
      },
      [HOME],
      NOW,
    );
    expect(charging).toMatchObject({ battery: 40, charging: true });
    expect(older.charging).toBeUndefined();
  });

  it('keeps HA when it is the fresher source', () => {
    const cards = buildFamilyCards(
      [person('sam', 'work', '2026-10-15T21:58:00Z')],
      { sam: { latitude: 39.4566, longitude: -104.4848, timestamp: sec('2026-10-15T14:00:00Z') } },
      [HOME, WORK],
      NOW,
    );
    expect(cards[0]).toMatchObject({ zone: 'Work', source: 'home_assistant' });
  });

  it('says Away outside every place, and In Motion from the app speed', () => {
    const [card] = buildFamilyCards(
      [person('alex', 'home', '2026-10-14T18:00:00Z')],
      { alex: { latitude: 39.54, longitude: -104.62, speed: 20, timestamp: sec('2026-10-15T21:59:50Z') } },
      [HOME, WORK],
      NOW,
    );
    expect(card).toMatchObject({ zone: 'Away', isMoving: true, speedMph: 45, inZones: [] });
  });

  it('matches a person by name token, not substring', () => {
    const cards = buildFamilyCards(
      [person('samantha_phone', 'home', '2026-10-14T18:00:00Z')],
      { sam: { latitude: 39.59, longitude: -104.82, timestamp: sec('2026-10-15T21:59:00Z') } },
      [WORK],
      NOW,
    );
    expect(cards.find((c) => c.id === 'person.samantha_phone')?.source).toBe('home_assistant');
    expect(cards.find((c) => c.id === 'user.sam')).toBeTruthy();
  });

  it('adds a card for an app user with no HA person, but not for a long-silent account', () => {
    const cards = buildFamilyCards(
      [],
      {
        leo: { latitude: 39.4566, longitude: -104.4848, timestamp: sec('2026-10-15T21:00:00Z') },
        default: { latitude: 39.39, longitude: -104.42, timestamp: sec('2026-09-28T00:00:00Z') },
      },
      [HOME],
      NOW,
    );
    expect(cards.map((c) => c.id)).toEqual(['user.leo']);
    expect(cards[0]).toMatchObject({ name: 'Leo', zone: 'Home', battery: null });
  });

  it('ignores a null-island or missing fix', () => {
    const cards = buildFamilyCards(
      [person('alex', 'home', '2026-10-14T18:00:00Z')],
      { alex: { latitude: 0, longitude: 0, timestamp: sec('2026-10-15T21:59:00Z') } },
      [HOME],
      NOW,
    );
    expect(cards[0].source).toBe('home_assistant');
  });
});

describe('placesAt', () => {
  it('lists the places containing a point, nearest first', () => {
    const big: Place = { name: 'Town', lat: 39.46, lon: -104.48, radius: 5000 };
    expect(placesAt(39.4566, -104.4848, [big, HOME])).toEqual(['Home', 'Town']);
  });
});

describe('groupByPlace', () => {
  const member = (over: Partial<FamilyMemberStatus> & { id: string; name: string }): FamilyMemberStatus => ({
    zone: 'Away',
    isMoving: false,
    speedMph: 0,
    battery: null,
    source: 'app',
    ...over,
  });

  it('groups people sharing a place and keeps everyone else on their own', () => {
    const groups = groupByPlace([
      member({ id: 'person.a', name: 'Ann', zone: 'School', inZones: ['zone.school'] }),
      member({ id: 'person.b', name: 'Ben', zone: 'School', inZones: ['zone.school'] }),
      member({ id: 'person.c', name: 'Cal', zone: 'Away' }),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0].key).toBe('zone.school');
    expect(groups[0].label).toBe('school');
    expect(groups[0].members.map((m) => m.name)).toEqual(['Ann', 'Ben']);
    expect(groups[1].key).toBe('solo:person.c');
    expect(groups[1].members).toHaveLength(1);
  });

  it('never merges two people who are both just away', () => {
    const groups = groupByPlace([
      member({ id: 'person.a', name: 'Ann', zone: 'Away' }),
      member({ id: 'person.b', name: 'Ben', zone: 'Away' }),
    ]);
    expect(groups.map((g) => g.key)).toEqual(['solo:person.a', 'solo:person.b']);
  });

  it('groups by the place name when no zone entity id is known', () => {
    const groups = groupByPlace([
      member({ id: 'person.a', name: 'Ann', zone: 'Grandma' }),
      member({ id: 'person.b', name: 'Ben', zone: 'Grandma' }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].key).toBe('place:grandma');
    expect(groups[0].members).toHaveLength(2);
  });
});
