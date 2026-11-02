import { Capacitor } from '@capacitor/core';
import { BleClient } from '@capacitor-community/bluetooth-le';

/**
 * Talk to the Jarvis Watch over its Nordic UART Service (NUS) GATT server.
 *
 * The watch advertises a single service; alongside the classic UART pair
 * (write a query, hear the answer as a notification) it carries a JSON health
 * snapshot and a one-line command channel. See the watch firmware's
 * `docs/ARCHITECTURE.md` for the wire contract.
 *
 * Two rules this module exists to keep:
 *
 *  1. The phone is the only side with a radio here, so every entry point is a
 *     no-op in a browser and nothing runs (or throws) at import time. A caller
 *     on a page that may be a web build does not have to branch first.
 *  2. A watch that is asleep, out of range, or on an older APK is not an
 *     error worth surfacing: reads return `null` and commands are logged, so
 *     the UI stays usable while the hardware is not.
 */

/** Nordic UART Service: the watch's one GATT service. */
export const NUS_SERVICE_UUID = '6e400001-b5a3-f393-e0a9-e50e24dcca9e';

/** Phone -> watch: one newline-terminated query; the answer arrives on TX. */
export const NUS_RX_CHARACTERISTIC_UUID = '6e400002-b5a3-f393-e0a9-e50e24dcca9e';

/** Watch -> phone: the answer to a query, newline-terminated, as a notify. */
export const NUS_TX_CHARACTERISTIC_UUID = '6e400003-b5a3-f393-e0a9-e50e24dcca9e';

/** Watch -> phone: a read-only JSON snapshot of the watch's counters. */
export const WATCH_STATS_CHARACTERISTIC_UUID = '6e400004-b5a3-f393-e0a9-e50e24dcca9e';

/** Phone -> watch: one command line per write (stayawake, sync, time, ...). */
export const WATCH_CONTROL_CHARACTERISTIC_UUID = '6e400005-b5a3-f393-e0a9-e50e24dcca9e';

/**
 * The JSON snapshot the watch serves on `6e400004`. Every field is present:
 * the firmware always fills the buffer, using -1 for "unknown" numerics such
 * as `left_h` when the battery estimate is not ready.
 */
export interface WatchStats {
  steps: number;
  goal: number;
  active_min: number;
  battery: number;
  volts: number;
  uptime: number;
  mode: string;
  synced: number;
  charging: boolean;
  unplugged: number;
  left_h: number;
  last_charge_h: number;
}

/** How long `askWatch` waits for the watch's answer before giving up. */
const ASK_TIMEOUT_MS = 8000;

/**
 * How long a connect or service discovery may take. A sleeping watch accepts
 * the link and then never answers, so without this the button spins until the
 * OS gives up, which is tens of seconds on Android.
 */
const CONNECT_TIMEOUT_MS = 15000;

/** Reject if `promise` has not settled in `ms`; otherwise pass it through. */
function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`${label} timed out after ${ms} ms`));
    }, ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err: unknown) => {
        clearTimeout(timer);
        reject(err instanceof Error ? err : new Error(String(err)));
      },
    );
  });
}

/** The device picked in `requestDevice`, kept so a later call reconnects. */
let deviceId: string | null = null;

/** Mirrors the plugin's connection state so callers need no async probe. */
let connected = false;

/**
 * Whether this build can talk BLE at all. False in a browser, where there is
 * no watch to reach; true on the app, where the plugin is bundled. Kept
 * synchronous so a render can read it directly.
 */
export function isWatchBleSupported(): boolean {
  return Capacitor.isNativePlatform();
}

/** The watch is connected (last connect succeeded and has not dropped). */
export function isWatchConnected(): boolean {
  return connected && deviceId !== null;
}

/** Listeners told the moment the link drops, so a UI can stop polling at once. */
type DisconnectListener = () => void;
const disconnectListeners = new Set<DisconnectListener>();

/** Subscribe to link drops. Returns an unsubscribe function. */
export function onWatchDisconnect(listener: DisconnectListener): () => void {
  disconnectListeners.add(listener);
  return () => {
    disconnectListeners.delete(listener);
  };
}

/** A drop clears the flag but keeps the id, so the next connect can retry it. */
function handleDisconnect(): void {
  connected = false;
  for (const listener of disconnectListeners) {
    // One bad listener must not stop the others hearing about the drop.
    try {
      listener();
    } catch (err) {
      console.warn('[watchBle] disconnect listener threw:', err);
    }
  }
}

/** Write one newline-terminated line to a characteristic; throws on failure. */
async function writeLine(characteristic: string, text: string): Promise<void> {
  if (!isWatchBleSupported() || !deviceId) {
    throw new Error('watch is not connected');
  }
  const bytes = new TextEncoder().encode(`${text}\n`);
  const value = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  await BleClient.write(deviceId, NUS_SERVICE_UUID, characteristic, value);
}

/**
 * Connect to the watch, asking the user to pick one the first time.
 *
 * The chosen device id is cached for the life of the page: a later call
 * reconnects silently, and only falls back to the picker if that id is gone
 * (the watch was reflashed, or the phone rebooted). Returns whether a
 * connection is now up rather than throwing, so a button can report it.
 */
export async function connectWatch(): Promise<boolean> {
  if (!isWatchBleSupported()) return false;

  try {
    // initialize() also asks for the Android runtime permissions; a failure
    // here means Bluetooth is off or refused, not a bug.
    await BleClient.initialize();

    // A watch the OS is still bonded to — the app was backgrounded, or the
    // page reloaded — comes back without a picker: the plugin lists the links
    // the platform already holds. Only a watch that is truly gone needs the
    // picker below.
    if (!deviceId) {
      try {
        const held = await BleClient.getConnectedDevices([NUS_SERVICE_UUID]);
        const match = held.find((device) => Boolean(device.deviceId));
        if (match) deviceId = match.deviceId;
      } catch {
        // Not implemented on every platform; the picker is the fallback.
      }
    }

    if (deviceId) {
      try {
        await withTimeout(BleClient.connect(deviceId, handleDisconnect), CONNECT_TIMEOUT_MS, 'connect');
        await withTimeout(BleClient.discoverServices(deviceId), CONNECT_TIMEOUT_MS, 'discover');
        connected = true;
        return true;
      } catch (err) {
        // The cached device is gone; forget it and scan afresh below.
        console.warn('[watchBle] reconnect failed, scanning instead:', err);
        // A half-open link would make the picker's connect fail too.
        try {
          await BleClient.disconnect(deviceId);
        } catch {
          // Already down; nothing to close.
        }
        deviceId = null;
      }
    }

    const device = await BleClient.requestDevice({
      services: [NUS_SERVICE_UUID],
      // Web needs the service listed here too, so the same options serve both.
      optionalServices: [NUS_SERVICE_UUID],
    });
    await withTimeout(BleClient.connect(device.deviceId, handleDisconnect), CONNECT_TIMEOUT_MS, 'connect');
    deviceId = device.deviceId;
    await withTimeout(BleClient.discoverServices(device.deviceId), CONNECT_TIMEOUT_MS, 'discover');
    connected = true;
    return true;
  } catch (err) {
    console.warn('[watchBle] connect failed:', err);
    connected = false;
    return false;
  }
}

/** Close the link. The device id is kept so a later connect can reconnect. */
export async function disconnectWatch(): Promise<void> {
  if (!isWatchBleSupported() || !deviceId) {
    connected = false;
    return;
  }
  try {
    await BleClient.disconnect(deviceId);
  } catch (err) {
    console.warn('[watchBle] disconnect failed:', err);
  } finally {
    connected = false;
  }
}

/**
 * Read the watch's JSON snapshot. Returns null on any failure (not connected,
 * read refused, or a body that is not JSON) rather than throwing, because an
 * unreadable snapshot just means the watch is not available right now.
 */
export async function readWatchStats(): Promise<WatchStats | null> {
  if (!isWatchBleSupported() || !deviceId) return null;
  try {
    const value = await BleClient.read(
      deviceId,
      NUS_SERVICE_UUID,
      WATCH_STATS_CHARACTERISTIC_UUID,
    );
    const text = new TextDecoder().decode(value);
    return normalizeStats(JSON.parse(text) as unknown);
  } catch {
    return null;
  }
}

/** A finite number, or `fallback`. The firmware uses -1 for "unknown". */
function num(value: unknown, fallback = -1): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

/**
 * Coerce a decoded snapshot into the shape the panel draws.
 *
 * The firmware always fills the buffer, but a truncated read or a future field
 * rename must not reach `stats.steps.toLocaleString()` and take the page down.
 * Every field is defaulted here, and a body with none of the fields the panel
 * needs is treated as "no snapshot" so the UI shows its waiting state instead
 * of a row of dashes.
 */
export function normalizeStats(raw: unknown): WatchStats | null {
  if (!raw || typeof raw !== 'object') return null;
  const source = raw as Record<string, unknown>;
  if (!('steps' in source) && !('battery' in source) && !('mode' in source)) return null;
  return {
    steps: num(source.steps, 0),
    goal: num(source.goal, 0),
    active_min: num(source.active_min, 0),
    battery: num(source.battery),
    volts: num(source.volts, 0),
    uptime: num(source.uptime),
    mode: typeof source.mode === 'string' ? source.mode : '',
    synced: num(source.synced, 0),
    charging: source.charging === true,
    unplugged: num(source.unplugged, 0),
    left_h: num(source.left_h),
    last_charge_h: num(source.last_charge_h),
  };
}

/**
 * Send one control line to `6e400005` (e.g. `stayawake`, `sync`, `set host x`).
 * A newline is appended because the firmware reads a line, and any trailing
 * newline it receives is stripped again. Failures are logged, not thrown.
 */
export async function sendWatchCommand(line: string): Promise<void> {
  try {
    await writeLine(WATCH_CONTROL_CHARACTERISTIC_UUID, line);
  } catch (err) {
    console.warn('[watchBle] command failed:', err);
  }
}

/** True while a query holds the notify channel; see `askWatch`. */
let asking = false;

/** Runners waiting for the channel, oldest first; see `askWatch`. */
const askQueue: Array<() => void> = [];

/**
 * Ask the watch a question and wait for its answer on `6e400003`.
 *
 * Notifications are started before the write so the answer cannot arrive
 * before we are listening, and stopped again afterwards so a later query
 * cannot resolve on a stale line. Queries take turns on the one notify
 * channel. Resolves with '' when there is no answer within the timeout,
 * keeping the caller free of try/catch.
 */
export function askWatch(query: string): Promise<string> {
  return new Promise<string>((resolve) => {
    const start = (): void => {
      asking = true;
      askWatchNow(query)
        .then(resolve)
        .catch(() => resolve(''))
        .finally(() => {
          asking = false;
          askQueue.shift()?.();
        });
    };
    // The first caller runs immediately, because the notify handler has to be
    // installed before this call returns: an answer that arrives right away
    // would otherwise be missed. Later callers queue instead of clobbering the
    // watch's single notify channel.
    if (asking) askQueue.push(start);
    else start();
  });
}

async function askWatchNow(query: string): Promise<string> {
  if (!isWatchBleSupported() || !deviceId) return '';
  const id = deviceId;

  let settle: ((answer: string) => void) | null = null;
  const answered = new Promise<string>((resolve) => {
    settle = resolve;
  });

  const onNotify = (value: DataView): void => {
    if (!settle) return;
    const answer = new TextDecoder().decode(value).replace(/[\r\n]+$/, '');
    settle(answer);
    settle = null;
  };

  let timer: ReturnType<typeof setTimeout> | null = null;
  try {
    await BleClient.startNotifications(
      id,
      NUS_SERVICE_UUID,
      NUS_TX_CHARACTERISTIC_UUID,
      onNotify,
    );
    await writeLine(NUS_RX_CHARACTERISTIC_UUID, query);
    return await Promise.race([
      answered,
      new Promise<string>((resolve) => {
        timer = setTimeout(() => resolve(''), ASK_TIMEOUT_MS);
      }),
    ]);
  } catch (err) {
    console.warn('[watchBle] ask failed:', err);
    return '';
  } finally {
    if (timer) clearTimeout(timer);
    try {
      await BleClient.stopNotifications(id, NUS_SERVICE_UUID, NUS_TX_CHARACTERISTIC_UUID);
    } catch {
      // The link may already be down; there is nothing left to stop.
    }
  }
}

/**
 * Show a notification on the watch. The watch splits the line on a single
 * '|', so newlines and pipes in either field are flattened first; without
 * that, a body containing a newline would be read as a second command.
 */
export async function notifyWatch(title: string, text: string): Promise<void> {
  const oneLine = (value: string): string => value.replace(/[\r\n|]+/g, ' ').trim();
  await sendWatchCommand(`notify ${oneLine(title)}|${oneLine(text)}`);
}

/**
 * Set the watch's clock. Defaults to now, so the common call needs no
 * argument; the firmware takes epoch seconds.
 */
export async function syncWatchTime(epoch?: number): Promise<void> {
  const seconds = epoch ?? Math.floor(Date.now() / 1000);
  await sendWatchCommand(`time ${seconds}`);
}
