import { describe, expect, it } from 'vitest';
import { motionLabel } from './familyCards';

const base = { isMoving: false, speedMph: 0, source: 'app' as const };

describe('motionLabel', () => {
  it('a measured speed while moving', () => {
    expect(motionLabel({ ...base, isMoving: true, speedMph: 42, speedKnown: true })).toBe('42 mph');
  });
  it('what the phone says when the fix has no speed, never a false "Stationary"', () => {
    expect(motionLabel({ ...base, speedKnown: false, activity: 'driving' })).toBe('Driving');
    expect(motionLabel({ ...base, speedKnown: false, activity: 'walking' })).toBe('Walking');
    expect(motionLabel({ ...base, speedKnown: false, activity: null })).toBeNull();
  });
  it('stationary when that is actually known', () => {
    expect(motionLabel({ ...base, speedKnown: true })).toBe('Stationary');
    expect(motionLabel({ ...base, speedKnown: false, activity: 'still' })).toBe('Stationary');
  });
});
