import { describe, expect, it } from 'vitest';
import { asOf, localAnswer, locationRows, provisionalShopping, shoppingRows } from './offlineAnswers';

const NOW = new Date(2026, 10, 24, 18, 0).getTime();
const at = new Date(2026, 10, 24, 15, 41).getTime();
const cache = [{ key: 'locations', version: at, data: { locations: [
  { username: 'gracie', display_name: 'Gracie Example', zone: 'School' },
  { username: 'sam', latitude: 39.7392, longitude: -104.9903 },
] } }];

describe('offline answers', () => {
  it('answers where someone is from the last shared location, with its age', () => {
    expect(localAnswer('Where is Gracie?', cache, NOW)).toBe('Gracie Example was at School (as of 3:41 PM, the last location this phone has).');
    expect(localAnswer("where's sam", cache, NOW)).toMatch(/39\.7392, -104\.9903 \(as of 3:41 PM/);
  });

  it('leaves everything else to Jarvis', () => {
    expect(localAnswer('Where is the nearest gas station near the highway exit please', cache, NOW)).toBeNull();
    expect(localAnswer('Turn off the lights', cache, NOW)).toBeNull();
    expect(localAnswer('Where is Robin?', cache, NOW)).toBeNull();
    expect(localAnswer('Where is Gracie?', [], NOW)).toBeNull();
  });

  it('says the date when the data is from another day', () => {
    expect(asOf(new Date(2026, 10, 22, 9, 5).getTime(), NOW)).toBe('as of Nov 22, 9:05 AM');
    expect(asOf(null)).toBe('time unknown');
  });

  it('reads location answers of any shape', () => {
    expect(locationRows([{ user: 'a' }])).toHaveLength(1);
    expect(locationRows({ a: { lat: 1, lon: 2 } })[0]).toMatchObject({ user: 'a', lat: 1 });
  });

  it('shows queued shopping edits provisionally until the server confirms them', () => {
    const rows = shoppingRows({ key: 'shopping', version: 1, data: { text: '- [ ] Milk\n- [x] Eggs\nnotes' } });
    expect(rows).toEqual([{ text: 'Milk', checked: false }, { text: 'Eggs', checked: true }]);
    const out = provisionalShopping(rows, [
      { kind: 'shopping_check', item: 'milk', status: 'waiting_for_online_phone' },
      { kind: 'shopping_add', item: 'Bread', status: 'no_family_phone_nearby' },
      { kind: 'shopping_add', item: 'eggs', status: 'waiting_for_online_phone' },
      { kind: 'shopping_add', item: 'Jam', status: 'expired' },
    ]);
    expect(out).toEqual([
      { text: 'Milk', checked: true, provisional: true },
      { text: 'Eggs', checked: true, provisional: false },
      { text: 'Bread', checked: false, provisional: true },
    ]);
  });
});
