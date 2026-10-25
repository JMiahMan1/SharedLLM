import { describe, it, expect } from 'vitest';
import { deviceKindLabel } from './deviceKind';

describe('deviceKindLabel', () => {
  it.each([
    ['phone', 'Phone'],
    ['tablet', 'Tablet'],
    ['watch', 'Watch'],
    ['assistant', 'Assistant'],
    ['light', 'Light'],
  ])('names %s as %s', (kind, label) => {
    expect(deviceKindLabel(kind)).toBe(label);
  });

  it('does not invent a name for a kind it does not know', () => {
    expect(deviceKindLabel('toaster')).toBe('Unknown');
    expect(deviceKindLabel('')).toBe('Unknown');
    expect(deviceKindLabel(null)).toBe('Unknown');
    expect(deviceKindLabel(undefined)).toBe('Unknown');
  });

  it('tolerates case and stray whitespace from the server', () => {
    expect(deviceKindLabel(' Tablet ')).toBe('Tablet');
    expect(deviceKindLabel('PHONE')).toBe('Phone');
  });
});
