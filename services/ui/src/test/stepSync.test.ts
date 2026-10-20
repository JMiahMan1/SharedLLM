import { describe, it, expect, vi, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const mocks = vi.hoisted(() => ({
  isNative: vi.fn(() => true),
  registerPlugin: vi.fn(),
}));

vi.mock('@capacitor/core', () => ({
  Capacitor: { isNativePlatform: mocks.isNative },
  registerPlugin: mocks.registerPlugin,
}));

import {
  getStepSync,
  startBackgroundSteps,
  stopBackgroundSteps,
  isBackgroundStepsRunning,
} from '../lib/stepSync';

const plugin = () => ({
  start: vi.fn(async () => ({ started: true })),
  stop: vi.fn(async () => undefined),
  isRunning: vi.fn(async () => ({ running: true })),
});

describe('background step sync', () => {
  let p: ReturnType<typeof plugin>;

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.isNative.mockReturnValue(true);
    p = plugin();
    mocks.registerPlugin.mockReturnValue(p);
  });

  it('hands step sync to the native service', async () => {
    const state = await startBackgroundSteps('michele', 'user-1');
    expect(p.start).toHaveBeenCalledWith({ username: 'michele', userId: 'user-1' });
    expect(state).toEqual({ serviceRunning: true, supported: true });
  });

  it('reports unsupported on a build without the plugin', async () => {
    mocks.registerPlugin.mockImplementation(() => {
      throw new Error('plugin is not implemented on android');
    });
    await expect(startBackgroundSteps('michele')).resolves.toEqual({
      serviceRunning: false,
      supported: false,
    });
  });

  it('never throws when the service refuses to start', async () => {
    p.start.mockRejectedValue(new Error('ForegroundServiceStartNotAllowedException'));
    await expect(startBackgroundSteps('michele')).resolves.toEqual({
      serviceRunning: false,
      supported: true,
    });
  });

  it('does nothing off-device', async () => {
    mocks.isNative.mockReturnValue(false);
    expect(getStepSync()).toBeNull();
    await startBackgroundSteps('michele');
    expect(mocks.registerPlugin).not.toHaveBeenCalled();
  });

  it('stops the service when steps are turned off', async () => {
    await stopBackgroundSteps();
    expect(p.stop).toHaveBeenCalled();
  });

  it('reports whether the service is actually alive', async () => {
    await expect(isBackgroundStepsRunning()).resolves.toBe(true);
  });

  it('does not claim running when the probe fails', async () => {
    p.isRunning.mockRejectedValue(new Error('bridge gone'));
    await expect(isBackgroundStepsRunning()).resolves.toBe(false);
  });
});

describe('LocationContext step wiring', () => {
  const source = readFileSync(
    resolve(process.cwd(), 'src/context/LocationContext.tsx'),
    'utf8',
  );

  it('starts the step service when location is not carrying the pedometer', () => {
    expect(source).toMatch(/await startBackgroundSteps\(user\)/);
    expect(source).toMatch(/if \(steps && user\)/);
  });

  it('stops the step service when steps are off or location takes over', () => {
    expect(source).toMatch(/await stopBackgroundSteps\(\)/);
  });

  it('keeps step sync alive when location tracking stops', () => {
    expect(source).toMatch(/void startBackgroundSteps\(user\)/);
  });
});
