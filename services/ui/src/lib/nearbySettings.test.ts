import { describe, expect, it } from 'vitest';
import { NEARBY_DEFAULTS, deviceLine, nearbyState } from './nearbySettings';
import type { NearbyDeviceState } from '../plugins/nearby';

const device: NearbyDeviceState = {
  nearby: true, chat: false, offline: false, car: true, parentLink: false,
  scanPermission: true, advertisePermission: true, connectPermission: true, bluetoothOn: true,
  host: 'location', apiLevel: 36,
};

describe('nearbySettings', () => {
  it('reads a partial or odd answer as the defaults', () => {
    expect(nearbyState(null).settings).toEqual(NEARBY_DEFAULTS);
    const s = nearbyState({ settings: { chat: true, car: 'no' }, managed_by_parent: true, chosen: true, always_on: ['parent_link', 3] });
    expect(s.settings).toEqual({ ...NEARBY_DEFAULTS, chat: true });
    expect(s.managed_by_parent).toBe(true);
    expect(s.can_change).toBe(false);
    expect(s.always_on).toEqual(['parent_link']);
  });

  it('says what keeps a part from working on this phone, or nothing', () => {
    const on = { ...NEARBY_DEFAULTS, nearby: true };
    expect(deviceLine(device, on)).toBeNull();
    expect(deviceLine({ ...device, bluetoothOn: false }, on)).toMatch(/Bluetooth is off/);
    expect(deviceLine({ ...device, scanPermission: false }, on)).toMatch(/Allow Nearby devices/);
    expect(deviceLine({ ...device, host: 'own' }, on)).toMatch(/location sharing is off/);
    // Nothing on (car detection off too): nothing to say.
    expect(deviceLine({ ...device, bluetoothOn: false }, { ...NEARBY_DEFAULTS, car: false })).toBeNull();
    // A child's parent link counts even with every switch off.
    expect(deviceLine({ ...device, connectPermission: false }, { ...NEARBY_DEFAULTS, car: false }, true)).toMatch(/pass messages/);
  });
});
