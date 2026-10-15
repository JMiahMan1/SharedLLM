import { describe, it, expect } from 'vitest';
import { buildFamilyCards, placesAt, type Place } from './familyCards';

const HOME: Place = { name: 'Home', lat: 33.1666, lon: -111.5648, radius: 100 };
const WORK: Place = { name: 'Work', lat: 33.3, lon: -111.9, radius: 150 };
const NOW = Date.parse('2026-10-15T22:00:00Z');
const sec = (iso: string) => Date.parse(iso) / 1000;

const person = (id: string, state: string, lastUpdated: string, extra: Record<string, unknown> = {}) => ({
  properties: { entity_id: `person.${id}`, friendly_name: id[0].toUpperCase() + id.slice(1), state, last_updated: lastUpdated, battery: 80, ...extra },
});

describe('buildFamilyCards', () => {
  it("takes the app's newer fix over a HA person that stopped following the phone", () => {
    const cards = buildFamilyCards(
      [person('jeremiah', 'home', '2026-10-14T18:00:00Z')],
      { jeremiah: { latitude: 33.3001, longitude: -111.9001, speed: 0, accuracy: 18, timestamp: sec('2026-10-15T21:59:30Z') } },
      [HOME, WORK],
      NOW,
    );
    expect(cards).toHaveLength(1);
    expect(cards[0]).toMatchObject({ id: 'person.jeremiah', zone: 'Work', source: 'app', accuracy: 18, battery: 80 });
    expect(cards[0].lastUpdated).toBe('2026-10-15T21:59:30.000Z');
  });

  it('keeps HA when it is the fresher source', () => {
    const cards = buildFamilyCards(
      [person('michele', 'work', '2026-10-15T21:58:00Z')],
      { michele: { latitude: 33.1666, longitude: -111.5648, timestamp: sec('2026-10-15T14:00:00Z') } },
      [HOME, WORK],
      NOW,
    );
    expect(cards[0]).toMatchObject({ zone: 'Work', source: 'home_assistant' });
  });

  it('says Away outside every place, and In Motion from the app speed', () => {
    const [card] = buildFamilyCards(
      [person('jeremiah', 'home', '2026-10-14T18:00:00Z')],
      { jeremiah: { latitude: 33.25, longitude: -111.7, speed: 20, timestamp: sec('2026-10-15T21:59:50Z') } },
      [HOME, WORK],
      NOW,
    );
    expect(card).toMatchObject({ zone: 'Away', isMoving: true, speedMph: 45, inZones: [] });
  });

  it('matches a person by name token, not substring', () => {
    const cards = buildFamilyCards(
      [person('michelle_phone', 'home', '2026-10-14T18:00:00Z')],
      { mich: { latitude: 33.3, longitude: -111.9, timestamp: sec('2026-10-15T21:59:00Z') } },
      [WORK],
      NOW,
    );
    expect(cards.find((c) => c.id === 'person.michelle_phone')?.source).toBe('home_assistant');
    expect(cards.find((c) => c.id === 'user.mich')).toBeTruthy();
  });

  it('adds a card for an app user with no HA person, but not for a long-silent account', () => {
    const cards = buildFamilyCards(
      [],
      {
        leo: { latitude: 33.1666, longitude: -111.5648, timestamp: sec('2026-10-15T21:00:00Z') },
        default: { latitude: 33.1, longitude: -111.5, timestamp: sec('2026-09-28T00:00:00Z') },
      },
      [HOME],
      NOW,
    );
    expect(cards.map((c) => c.id)).toEqual(['user.leo']);
    expect(cards[0]).toMatchObject({ name: 'Leo', zone: 'Home', battery: null });
  });

  it('ignores a null-island or missing fix', () => {
    const cards = buildFamilyCards(
      [person('jeremiah', 'home', '2026-10-14T18:00:00Z')],
      { jeremiah: { latitude: 0, longitude: 0, timestamp: sec('2026-10-15T21:59:00Z') } },
      [HOME],
      NOW,
    );
    expect(cards[0].source).toBe('home_assistant');
  });
});

describe('placesAt', () => {
  it('lists the places containing a point, nearest first', () => {
    const big: Place = { name: 'Town', lat: 33.17, lon: -111.56, radius: 5000 };
    expect(placesAt(33.1666, -111.5648, [big, HOME])).toEqual(['Home', 'Town']);
  });
});
