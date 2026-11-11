import { describe, it, expect } from 'vitest';
import { deviceKindLabel, deviceNoun, devicePlatformLabel, handheldNoun, isEsphomeDevice, thisDeviceNoun } from './deviceKind';

describe('deviceKindLabel', () => {
  it.each([
    ['phone', 'Phone'],
    ['tablet', 'Tablet'],
    ['watch', 'Watch'],
    ['speaker', 'Smart speaker'],
    ['screen', 'Smart screen'],
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

describe('handheldNoun', () => {
  it.each([
    [['phone'], 'phone'],
    [['tablet'], 'tablet'],
    [['tablet', 'phone'], 'phone or tablet'],
    [['watch'], 'device'],
    [[], 'device'],
    [undefined, 'device'],
  ] as const)('%j is a %s', (kinds, noun) => {
    expect(handheldNoun(kinds as readonly string[] | undefined)).toBe(noun);
  });
});

describe('thisDeviceNoun', () => {
  it('uses the form factor, and "device" when there is none', () => {
    expect(thisDeviceNoun('tablet')).toBe('tablet');
    expect(thisDeviceNoun('phone')).toBe('phone');
    expect(thisDeviceNoun(null)).toBe('device');
  });
});

describe('windows-pc', () => {
  it('is named and platformed as a Windows PC, never an Android', () => {
    expect(deviceKindLabel('windows-pc')).toBe('Windows PC');
    expect(deviceKindLabel(' WINDOWS-PC ')).toBe('Windows PC');
    expect(deviceNoun('windows-pc')).toBe('Windows PC');
    expect(devicePlatformLabel({ kind: 'windows-pc' })).toBe('Windows PC');
    expect(isEsphomeDevice({ kind: 'windows-pc' })).toBe(false);
  });

  it('is described as a device, never a phone or tablet, in family sentences', () => {
    expect(handheldNoun(['windows-pc'])).toBe('device');
    expect(handheldNoun(['windows-pc', 'tablet'])).toBe('tablet');
  });
});
