import { describe, it, expect, vi, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const mocks = vi.hoisted(() => ({
  isNative: vi.fn(() => true),
  registerPlugin: vi.fn(),
  setIdentity: vi.fn(async () => undefined),
}));

vi.mock('@capacitor/core', () => ({
  Capacitor: { isNativePlatform: mocks.isNative },
  registerPlugin: mocks.registerPlugin,
}));

vi.mock('../plugins/tokenBridge', () => ({
  default: { setIdentity: mocks.setIdentity },
}));

import {
  getLocationTracking,
  startBackgroundTracking,
  stopBackgroundTracking,
  isBackgroundTrackingRunning,
  mirrorIdentity,
} from '../lib/locationTracking';

const plugin = () => ({
  start: vi.fn(async () => ({ started: true })),
  stop: vi.fn(async () => undefined),
  isRunning: vi.fn(async () => ({ running: true })),
});

describe('background location tracking', () => {
  let p: ReturnType<typeof plugin>;

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.isNative.mockReturnValue(true);
    p = plugin();
    mocks.registerPlugin.mockReturnValue(p);
  });

  it('hands tracking to the native service', async () => {
    const state = await startBackgroundTracking('michele');
    expect(p.start).toHaveBeenCalledWith({ username: 'michele', userId: undefined });
    expect(state).toEqual({ serviceRunning: true, supported: true });
  });

  // A web bundle can be newer than the installed APK, so the plugin may simply
  // not exist yet. That must degrade, not throw.
  it('reports unsupported on a build without the plugin', async () => {
    mocks.registerPlugin.mockImplementation(() => {
      throw new Error('plugin is not implemented on android');
    });
    await expect(startBackgroundTracking('michele')).resolves.toEqual({
      serviceRunning: false,
      supported: false,
    });
  });

  it('never throws when the service refuses to start', async () => {
    p.start.mockRejectedValue(new Error('ForegroundServiceStartNotAllowedException'));
    await expect(startBackgroundTracking('michele')).resolves.toEqual({
      serviceRunning: false,
      supported: true,
    });
  });

  it('does nothing off-device', async () => {
    mocks.isNative.mockReturnValue(false);
    expect(getLocationTracking()).toBeNull();
    await startBackgroundTracking('michele');
    expect(mocks.registerPlugin).not.toHaveBeenCalled();
  });

  it('stops the service, so opting out really stops uploading', async () => {
    await stopBackgroundTracking();
    expect(p.stop).toHaveBeenCalled();
  });

  it('reports whether the service is actually alive', async () => {
    await expect(isBackgroundTrackingRunning()).resolves.toBe(true);
  });

  it('does not claim running when the probe fails', async () => {
    p.isRunning.mockRejectedValue(new Error('bridge gone'));
    await expect(isBackgroundTrackingRunning()).resolves.toBe(false);
  });

  it('mirrors the identity natively for the service to attribute uploads', async () => {
    await mirrorIdentity('michele');
    expect(mocks.setIdentity).toHaveBeenCalledWith({ username: 'michele', userId: undefined });
  });

  it('does not mirror identity off-device', async () => {
    mocks.isNative.mockReturnValue(false);
    await mirrorIdentity('michele');
    expect(mocks.setIdentity).not.toHaveBeenCalled();
  });
});

/**
 * The decision function tests above would all pass even if LocationContext
 * never called this module -- the same trap as the heartbeat fix. So pin the
 * call sites in the real source.
 */
describe('LocationContext wiring', () => {
  const source = readFileSync(
    resolve(process.cwd(), 'src/context/LocationContext.tsx'),
    'utf8',
  );
  const auth = readFileSync(resolve(process.cwd(), 'src/context/AuthContext.tsx'), 'utf8');

  it('starts background tracking when the watch comes up', () => {
    expect(source).toMatch(/await startBackgroundTracking\(/);
  });

  it('stops the service when tracking is turned off', () => {
    // A sticky foreground service survives this context, so opting out has to
    // stop it explicitly or it keeps uploading for someone who just left.
    expect(source).toMatch(/void stopBackgroundTracking\(\)/);
  });

  it('mirrors the identity on login', () => {
    expect(auth).toMatch(/mirrorIdentity\(/);
  });

  it('uses the profile username, not the typed one, for the native mirror', () => {
    expect(auth).toMatch(/mirrorIdentity\(canonical\)/);
  });
});
