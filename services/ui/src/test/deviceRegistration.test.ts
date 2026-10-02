/**
 * Device self-registration: a phone reports what it is on login, so the server
 * can answer "which build is this device on?" without guessing.
 *
 * The rule these tests defend is that telemetry must never stand between a
 * user and their app -- every failure path has to resolve, not reject.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  registerThisDevice,
  resolveDeviceKey,
  getDeviceKey,
  __setImpl,
  type DeviceRegistration,
} from '../lib/deviceRegistration';

const native = (over: Partial<Parameters<typeof __setImpl>[0]> = {}) =>
  __setImpl({
    isNative: () => true,
    getAppInfo: async () => ({ version: '1.5.0', build: '24' }),
    getDeviceInfo: async () => ({ model: 'Pixel 7', manufacturer: 'Google', osVersion: '14' }),
    post: async () => {},
    ...over,
  });

describe('resolveDeviceKey', () => {
  it('keeps a stored id so a device does not re-register as new', () => {
    const create = vi.fn(() => 'generated');
    expect(resolveDeviceKey('phone-abcdef0123456789', create)).toBe('phone-abcdef0123456789');
    expect(create).not.toHaveBeenCalled();
  });

  it('creates one when there is nothing stored', () => {
    expect(resolveDeviceKey(null, () => 'phone-new')).toBe('phone-new');
  });

  it('distrusts a stored value too short to be ours', () => {
    // Anything under 16 chars did not come from randomId(), so treating it as
    // an identity would merge two devices onto one row.
    expect(resolveDeviceKey('short', () => 'phone-new')).toBe('phone-new');
  });
});

describe('getDeviceKey', () => {
  beforeEach(() => localStorage.clear());

  it('persists a generated id and reuses it afterwards', async () => {
    const first = await getDeviceKey();
    expect(first.length).toBeGreaterThanOrEqual(16);
    expect(await getDeviceKey()).toBe(first);
  });
});

describe('registerThisDevice', () => {
  beforeEach(() => localStorage.clear());
  let restore: () => void = () => {};
  afterEach(() => {
    restore();
    vi.restoreAllMocks();
  });

  it('sends the running build number, which is what the update check compares', async () => {
    let sent: DeviceRegistration | null = null;
    restore = native({ post: async (p) => { sent = p; } });

    const result = await registerThisDevice();

    expect(result.registered).toBe(true);
    expect(sent!.app_build).toBe('24');
    expect(sent!.app_version).toBe('1.5.0');
  });

  it('sends hardware details when they are available', async () => {
    let sent: DeviceRegistration | null = null;
    restore = native({ post: async (p) => { sent = p; } });

    await registerThisDevice();

    expect(sent!.model).toBe('Pixel 7');
    expect(sent!.os_version).toBe('14');
  });

  it('still registers when the hardware plugin is missing', async () => {
    let sent: DeviceRegistration | null = null;
    restore = native({
      getDeviceInfo: async () => { throw new Error('not installed'); },
      post: async (p) => { sent = p; },
    });

    const result = await registerThisDevice();

    expect(result.registered).toBe(true);
    expect(sent!.device_key).toBeTruthy();
    expect(sent!.model).toBeUndefined();
  });

  it('still registers when the app info cannot be read', async () => {
    let sent: DeviceRegistration | null = null;
    restore = native({
      getAppInfo: async () => { throw new Error('no native shell'); },
      post: async (p) => { sent = p; },
    });

    const result = await registerThisDevice();

    expect(result.registered).toBe(true);
    expect(sent!.device_key).toBeTruthy();
    expect(sent!.app_build).toBeUndefined();
  });

  it('never sends location or health', async () => {
    let sent: DeviceRegistration | null = null;
    restore = native({ post: async (p) => { sent = p; } });

    await registerThisDevice();

    // The payload type has no such fields; assert on the wire to be sure a
    // future edit cannot quietly add one.
    expect(Object.keys(sent!).sort()).toEqual(
      ['app_build', 'app_version', 'device_key', 'manufacturer', 'model', 'os_version'].sort(),
    );
  });

  // The whole point of returning instead of throwing.
  it('resolves rather than rejecting when the request fails', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    restore = native({ post: async () => { throw new Error('offline'); } });

    await expect(registerThisDevice()).resolves.toEqual({
      registered: false,
      reason: 'request failed',
    });
  });

  it('does nothing in a browser, and does not call the server', async () => {
    const post = vi.fn();
    restore = native({ isNative: () => false, post });

    await expect(registerThisDevice()).resolves.toEqual({
      registered: false,
      reason: 'not a native platform',
    });
    expect(post).not.toHaveBeenCalled();
  });
});
