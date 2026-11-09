import { beforeEach, describe, expect, it, vi } from 'vitest';

const { native, bridge } = vi.hoisted(() => ({
  native: { value: false },
  bridge: { getDeviceId: vi.fn() },
}));

vi.mock('@capacitor/core', () => ({ Capacitor: { isNativePlatform: () => native.value } }));
vi.mock('../plugins/tokenBridge', () => ({ default: bridge }));

import { browserDeviceId, locationDevice, resetLocationDeviceForTests } from './locationDevice';

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
  };
}

describe('locationDevice', () => {
  beforeEach(() => {
    native.value = false;
    bridge.getDeviceId.mockReset();
    resetLocationDeviceForTests();
    window.localStorage.clear();
  });

  it('keeps one id per browser', () => {
    const storage = memoryStorage();
    const first = browserDeviceId(storage);
    expect(first).toBeTruthy();
    expect(browserDeviceId(storage)).toBe(first);
  });

  it('still names the upload when storage is unavailable', () => {
    const broken = {
      getItem: () => { throw new Error('private mode'); },
      setItem: () => { throw new Error('private mode'); },
    };
    expect(browserDeviceId(broken)).toBeTruthy();
  });

  it('uses the install id in the app, so the open app is not a second device', async () => {
    native.value = true;
    bridge.getDeviceId.mockResolvedValue({ deviceId: 'install-123', deviceName: 'Google Pixel 10' });
    expect(await locationDevice()).toEqual({ deviceId: 'install-123', deviceName: 'Google Pixel 10' });
  });

  it('falls back to a stored id on an app shell without the bridge method', async () => {
    native.value = true;
    bridge.getDeviceId.mockRejectedValue(new Error('not implemented'));
    const device = await locationDevice();
    expect(device.deviceName).toBe('Web browser');
    expect(device.deviceId).toBe(window.localStorage.getItem('jarvis_location_device_id'));
  });
});
