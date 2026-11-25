import { describe, expect, it } from 'vitest';
import { bandLabel, nearOf, togetherLine } from './proximity';

describe('proximity words', () => {
  it('says bands, never metres', () => {
    expect(bandLabel('next_to')).toBe('Next to you');
    expect(bandLabel('same_room')).toBe('In the same room');
    expect(bandLabel('nearby')).toBe('Nearby');
    expect(bandLabel('3m')).toBe('Unknown');
  });

  it('names who a trip was with, and the car when one is known', () => {
    expect(togetherLine([{ user_id: 'gracie', user_name: 'Gracie', via: ['same_vehicle'], vehicle_name: 'Odyssey' }]))
      .toBe('with Gracie in the Odyssey');
    expect(togetherLine([
      { user_id: 'gracie', user_name: 'Gracie', via: ['nearby'] },
      { user_id: 'alex', via: ['nearby'] },
      { user_id: 'sam', user_name: 'Sam', via: ['nearby'] },
    ], (id) => (id === 'alex' ? 'Alex' : undefined))).toBe('with Gracie, Alex and Sam');
    expect(togetherLine([])).toBeNull();
    expect(togetherLine(undefined)).toBeNull();
  });

  it('finds a person in the near-now list whatever their id looks like', () => {
    const near = [{ user_id: 'gracie', band: 'same_room' as const, last_seen: 1 }];
    expect(nearOf(near, 'person.Gracie')?.band).toBe('same_room');
    expect(nearOf(near, 'alex')).toBeUndefined();
  });
});
