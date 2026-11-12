import { describe, it, expect } from 'vitest';
import { buildFamilyCards, roomLabel } from './familyCards';

const person = (id: string, extra: Record<string, unknown> = {}) => ({
  properties: { entity_id: `person.${id}`, friendly_name: id[0].toUpperCase() + id.slice(1), state: 'home',
    last_updated: '2026-10-15T21:59:00Z', ...extra },
});

describe('the room on a family card', () => {
  it('carries the room the hub names, with when it was heard', () => {
    const [card] = buildFamilyCards([person('alex', { room: 'Den', room_area_id: 'den', room_last_seen: '2026-10-15T21:58:00Z' })], {}, []);
    expect(card).toMatchObject({ room: 'Den', roomLastSeen: '2026-10-15T21:58:00Z', roomStale: false });
    expect(roomLabel(card)).toBe('Den');
  });

  it('has no room when the hub does not know one (absent, not_home, unknown)', () => {
    const cards = buildFamilyCards([
      person('alex'),
      person('sam', { room: 'not_home' }),
      person('robin', { room: 'unknown' }),
    ], {}, []);
    for (const c of cards) {
      expect(c.room).toBeUndefined();
      expect(roomLabel(c)).toBeNull();
    }
  });

  it('never shows a stale room as current', () => {
    const [card] = buildFamilyCards([person('sam', { room: 'Loft', room_last_seen: '2026-10-15T21:00:00Z', room_stale: true })], {}, []);
    expect(roomLabel(card, () => '59 min ago')).toBe('Last seen in Loft, 59 min ago');
    expect(roomLabel(card)).toBe('Last seen in Loft');
  });

  it('keeps the room when a newer app fix updates the card', () => {
    const [card] = buildFamilyCards(
      [person('alex', { room: 'Den' })],
      { alex: { latitude: 39.45, longitude: -104.48, timestamp: Date.parse('2026-10-15T21:59:30Z') / 1000 } },
      [],
      Date.parse('2026-10-15T22:00:00Z'),
    );
    expect(card.source).toBe('app');
    expect(card.room).toBe('Den');
  });

  it('says nothing about the room of someone who paused sharing', () => {
    expect(roomLabel({ room: 'Den', paused: true })).toBeNull();
  });
});
