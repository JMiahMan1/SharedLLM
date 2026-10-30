import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const STATS = {
  steps: 1335,
  goal: 10000,
  active_min: 12,
  battery: 96,
  volts: 4.03,
  uptime: 1234,
  mode: 'deep',
  synced: 1793387672,
  charging: false,
  unplugged: 1793300000,
  left_h: 31,
  last_charge_h: 2.4,
};

const mocks = vi.hoisted(() => {
  const state: { notify?: (value: DataView) => void } = {};
  return {
    state,
    isNative: vi.fn(() => true),
    initialize: vi.fn(async () => undefined),
    requestDevice: vi.fn(async () => ({ deviceId: 'AA:BB:CC', name: 'Jarvis Watch' })),
    connect: vi.fn<(id: string, onDisconnect?: (id: string) => void) => Promise<void>>(
      async () => undefined,
    ),
    disconnect: vi.fn<(id: string) => Promise<void>>(async () => undefined),
    discoverServices: vi.fn<(id: string) => Promise<void>>(async () => undefined),
    read: vi.fn<(id: string, s: string, c: string) => Promise<DataView>>(
      async () => new DataView(new TextEncoder().encode(JSON.stringify(STATS)).buffer),
    ),
    write: vi.fn<(id: string, s: string, c: string, v: DataView) => Promise<void>>(
      async () => undefined,
    ),
    startNotifications: vi.fn<
      (id: string, s: string, c: string, cb: (value: DataView) => void) => Promise<void>
    >(async (...args) => {
      state.notify = args[3];
    }),
    stopNotifications: vi.fn<(id: string, s: string, c: string) => Promise<void>>(
      async () => undefined,
    ),
  };
});

vi.mock('@capacitor/core', () => ({
  Capacitor: { isNativePlatform: mocks.isNative },
}));

vi.mock('@capacitor-community/bluetooth-le', () => ({
  BleClient: {
    initialize: mocks.initialize,
    requestDevice: mocks.requestDevice,
    connect: mocks.connect,
    disconnect: mocks.disconnect,
    discoverServices: mocks.discoverServices,
    read: mocks.read,
    write: mocks.write,
    startNotifications: mocks.startNotifications,
    stopNotifications: mocks.stopNotifications,
  },
}));

import {
  NUS_SERVICE_UUID,
  NUS_RX_CHARACTERISTIC_UUID,
  NUS_TX_CHARACTERISTIC_UUID,
  WATCH_STATS_CHARACTERISTIC_UUID,
  WATCH_CONTROL_CHARACTERISTIC_UUID,
} from '../lib/watchBle';

/** A fresh module instance, so the cached device id does not leak between tests. */
async function load() {
  vi.resetModules();
  return await import('../lib/watchBle');
}

const decode = (view: DataView): string => new TextDecoder().decode(view);

describe('watchBle', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.state.notify = undefined;
    mocks.isNative.mockReturnValue(true);
    mocks.read.mockResolvedValue(new DataView(new TextEncoder().encode(JSON.stringify(STATS)).buffer));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('is a no-op off-device and never reaches the plugin', async () => {
    mocks.isNative.mockReturnValue(false);
    const watch = await load();

    expect(watch.isWatchBleSupported()).toBe(false);
    await expect(watch.connectWatch()).resolves.toBe(false);
    await expect(watch.readWatchStats()).resolves.toBeNull();
    await expect(watch.askWatch('hello')).resolves.toBe('');
    await watch.sendWatchCommand('sync');

    expect(mocks.initialize).not.toHaveBeenCalled();
    expect(mocks.requestDevice).not.toHaveBeenCalled();
    expect(mocks.write).not.toHaveBeenCalled();
  });

  it('connects to a device filtered by the NUS service and caches its id', async () => {
    const watch = await load();
    await expect(watch.connectWatch()).resolves.toBe(true);

    expect(mocks.requestDevice).toHaveBeenCalledWith({
      services: [NUS_SERVICE_UUID],
      optionalServices: [NUS_SERVICE_UUID],
    });
    expect(mocks.connect).toHaveBeenCalledWith('AA:BB:CC', expect.any(Function));
    expect(mocks.discoverServices).toHaveBeenCalledWith('AA:BB:CC');
    expect(watch.isWatchConnected()).toBe(true);

    // A later call reconnects to the cached id instead of scanning again.
    mocks.requestDevice.mockClear();
    await watch.disconnectWatch();
    await expect(watch.connectWatch()).resolves.toBe(true);
    expect(mocks.requestDevice).not.toHaveBeenCalled();
    expect(mocks.connect).toHaveBeenLastCalledWith('AA:BB:CC', expect.any(Function));
  });

  it('reports failure instead of throwing when the connection cannot be made', async () => {
    const watch = await load();
    mocks.requestDevice.mockRejectedValueOnce(new Error('user cancelled'));
    await expect(watch.connectWatch()).resolves.toBe(false);
    expect(watch.isWatchConnected()).toBe(false);
  });

  it('parses the JSON snapshot from 6e400004', async () => {
    const watch = await load();
    await watch.connectWatch();
    await expect(watch.readWatchStats()).resolves.toEqual(STATS);
    expect(mocks.read).toHaveBeenCalledWith(
      'AA:BB:CC',
      NUS_SERVICE_UUID,
      WATCH_STATS_CHARACTERISTIC_UUID,
    );
  });

  it('returns null when the snapshot is unreadable', async () => {
    const watch = await load();
    await watch.connectWatch();

    mocks.read.mockResolvedValueOnce(new DataView(new TextEncoder().encode('not json').buffer));
    await expect(watch.readWatchStats()).resolves.toBeNull();

    mocks.read.mockRejectedValueOnce(new Error('GATT error'));
    await expect(watch.readWatchStats()).resolves.toBeNull();
  });

  it('writes a newline-terminated command to 6e400005', async () => {
    const watch = await load();
    await watch.connectWatch();
    await watch.sendWatchCommand('stayawake');

    const [, , characteristic, value] = mocks.write.mock.calls[0];
    expect(characteristic).toBe(WATCH_CONTROL_CHARACTERISTIC_UUID);
    expect(decode(value)).toBe('stayawake\n');
  });

  it('asks a query on 6e400002 and resolves with the next notify on 6e400003', async () => {
    const watch = await load();
    await watch.connectWatch();

    const pending = watch.askWatch('what time is it');
    // startNotifications ran synchronously, so the callback is already held.
    mocks.state.notify?.(new DataView(new TextEncoder().encode('It is noon\n').buffer));

    await expect(pending).resolves.toBe('It is noon');
    expect(mocks.write).toHaveBeenCalledWith(
      'AA:BB:CC',
      NUS_SERVICE_UUID,
      NUS_RX_CHARACTERISTIC_UUID,
      expect.any(DataView),
    );
    expect(mocks.stopNotifications).toHaveBeenCalledWith(
      'AA:BB:CC',
      NUS_SERVICE_UUID,
      NUS_TX_CHARACTERISTIC_UUID,
    );
  });

  it('gives up on a query the watch never answers', async () => {
    const watch = await load();
    await watch.connectWatch();

    vi.useFakeTimers();
    const pending = watch.askWatch('anyone there');
    await vi.advanceTimersByTimeAsync(8000);
    await expect(pending).resolves.toBe('');
  });

  it('flattens a notification into one command line', async () => {
    const watch = await load();
    await watch.connectWatch();
    await watch.notifyWatch('Team\n|alert', 'Line one\nLine two');

    const [, , characteristic, value] = mocks.write.mock.calls[0];
    expect(characteristic).toBe(WATCH_CONTROL_CHARACTERISTIC_UUID);
    expect(decode(value)).toBe('notify Team alert|Line one Line two\n');
  });

  it('sets the watch clock, defaulting to now', async () => {
    const watch = await load();
    await watch.connectWatch();

    await watch.syncWatchTime(1700000000);
    expect(decode(mocks.write.mock.calls[0][3])).toBe('time 1700000000\n');

    const now = vi.spyOn(Date, 'now').mockReturnValue(1700000000_000);
    await watch.syncWatchTime();
    expect(decode(mocks.write.mock.calls[1][3])).toBe('time 1700000000\n');
    now.mockRestore();
  });
});
