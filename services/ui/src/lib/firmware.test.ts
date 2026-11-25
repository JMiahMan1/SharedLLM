import { describe, it, expect } from 'vitest';
import { firmwareLine, firmwareStatus, versionText, type FirmwareRow } from './firmware';

const base: FirmwareRow = {
  id: 'update.kitchen_firmware',
  entity_id: 'update.kitchen_firmware',
  device_id: 'dev-kitchen',
  device: 'Kitchen panel',
  name: 'Firmware',
  integration: 'esphome',
  model: 'ESP32-S3',
  area_id: 'kitchen',
  installed_version: '2026.10.17',
  latest_version: '2026.11.18',
  available: true,
  installable: true,
  in_progress: false,
  release_url: null,
  source: 'entity',
  action: 'install',
};

describe('firmware helpers', () => {
  it('names a missing version rather than leaving a gap', () => {
    expect(versionText('')).toBe('not reported');
    expect(versionText(null)).toBe('not reported');
    expect(versionText(' 1.0 ')).toBe('1.0');
  });

  it('shows what it runs and what it would move to when an update is offered', () => {
    expect(firmwareLine(base)).toBe('2026.10.17 to 2026.11.18');
    expect(firmwareLine({ ...base, available: false })).toBe('2026.10.17');
  });

  it('says where a device-only row gets its image', () => {
    expect(firmwareLine({ ...base, available: false, action: 'upgrade', installed_version: '15.6.0' })).toBe(
      "15.6.0, updated from the device's own update address",
    );
  });

  it('reads the badge from what the row can do', () => {
    expect(firmwareStatus(base)).toBe('available');
    expect(firmwareStatus({ ...base, available: false })).toBe('current');
    expect(firmwareStatus({ ...base, in_progress: true })).toBe('busy');
    expect(firmwareStatus({ ...base, available: false, action: 'upgrade', upgradable: true })).toBe('manual');
    expect(firmwareStatus({ ...base, available: false, action: 'upgrade', upgradable: false })).toBe('unknown');
  });

  it('keeps a running install ahead of the offer it is fulfilling', () => {
    expect(firmwareStatus({ ...base, available: true, in_progress: true })).toBe('busy');
  });
});
