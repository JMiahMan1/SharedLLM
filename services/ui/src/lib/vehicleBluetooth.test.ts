import { describe, expect, it } from 'vitest';
import type { BondedDevice } from '../plugins/vehicleBluetooth';
import { deviceRows, linkLabel, normaliseAddress, shortAddress, vehicleOfAddress, vehicleSourceLabel } from './vehicleBluetooth';

// Made-up addresses and names.
const HEAD_UNIT = '00:1A:7D:DA:71:13';
const vehicles = [
  { id: 'van', name: 'Van', bluetooth: [{ address: HEAD_UNIT, name: 'Car Audio', linked_by: 'pat' }] },
  { id: 'truck', name: 'Truck', bluetooth: [] },
];
const device = (over: Partial<BondedDevice>): BondedDevice => ({
  name: 'Thing', address: '00:00:00:00:00:01', type: 'classic', connected: false,
  linkable: true, kind: 'classic', reason: null, linkedTo: null, ...over,
});

describe('vehicle Bluetooth', () => {
  it('normalises addresses and refuses hidden ones', () => {
    expect(normaliseAddress('00-1a-7d-da-71-13')).toBe(HEAD_UNIT);
    expect(normaliseAddress('001A7DDA7113')).toBe(HEAD_UNIT);
    expect(normaliseAddress('02:00:00:00:00:00')).toBeNull();
    expect(normaliseAddress('Car Audio')).toBeNull();
    expect(shortAddress(HEAD_UNIT)).toBe('...DA:71:13');
  });

  it('finds a vehicle by address, not by the name a device has now', () => {
    expect(vehicleOfAddress(vehicles, '00:1a:7d:da:71:13')?.id).toBe('van');
    expect(vehicleOfAddress(vehicles, '00:00:00:00:00:02')).toBeUndefined();
    expect(linkLabel({ address: HEAD_UNIT, name: '' })).toBe('Device ...DA:71:13');
  });

  it('labels how a trip got its vehicle', () => {
    expect(vehicleSourceLabel('bluetooth')).toBe('Detected by Bluetooth');
    expect(vehicleSourceLabel('manual')).toBe('Chosen by hand');
    expect(vehicleSourceLabel('default')).toBeNull();
    expect(vehicleSourceLabel(undefined)).toBeNull();
  });

  it('lists connected linkable devices first and says why the others cannot be linked', () => {
    const rows = deviceRows(
      [
        device({ name: 'Earbuds', address: '00:00:00:00:00:05' }),
        device({ name: 'Watch', address: '4A:11:22:33:44:55', type: 'le', linkable: false, kind: null, reason: 'keeps changing its address' }),
        device({ name: 'Renamed Head Unit', address: HEAD_UNIT, connected: true }),
        device({ name: 'Truck Radio', address: '00:00:00:00:00:09', connected: true }),
      ],
      vehicles[1],
      vehicles,
    );
    expect(rows.map((r) => r.name)).toEqual(['Truck Radio', 'Renamed Head Unit', 'Earbuds', 'Watch']);
    const byName = Object.fromEntries(rows.map((r) => [r.name, r]));
    expect(byName['Truck Radio'].pickable).toBe(true);
    expect(byName['Truck Radio'].note).toBe('Connected now');
    expect(byName['Renamed Head Unit'].pickable).toBe(false);
    expect(byName['Renamed Head Unit'].note).toBe('Linked to Van');
    expect(byName.Watch.pickable).toBe(false);
    expect(byName.Watch.note).toMatch(/changing its address/);
  });

  it('marks a device already on this vehicle as linked here', () => {
    const [row] = deviceRows([device({ address: HEAD_UNIT })], vehicles[0], vehicles);
    expect(row.linkedHere).toBe(true);
    expect(row.pickable).toBe(false);
    expect(row.note).toBe('Linked to this vehicle');
  });
});
