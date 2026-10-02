/**
 * Device self-registration.
 *
 * On login the app reports what it is — a stable per-install id, the model,
 * the OS, and the APK version code — so the server can answer "which build is
 * this device on?" and "does it need an update?" without guessing.
 *
 * Two rules this module exists to enforce:
 *
 *  1. Registration is best-effort and must never block or break login. A phone
 *     that cannot register is still allowed to use the app; the failure is
 *     logged, not surfaced.
 *  2. Only metadata is sent. No location, no health, no identifiers belonging
 *     to anyone else. The server rejects anything outside its allowlist anyway.
 */
import { Capacitor } from '@capacitor/core';
import { getServerOrigin } from './serverUrl';

/**
 * Derive a stable id for this install, generated once and then persisted.
 *
 * `@capacitor/device` is not in package.json, so `Device.getId()` is not
 * available to us; this stands in for it. Split out as a pure function of the
 * store so the behaviour can be tested without a WebView.
 */
export function resolveDeviceKey(stored: string | null, create: () => string): string {
  // Anything shorter than 16 chars is not something we wrote, so treat it as
  // absent rather than trusting it as a device identity.
  if (stored && stored.length >= 16) return stored;
  return create();
}

/** The fields the server accepts. Metadata only, deliberately. */
export interface DeviceRegistration {
  device_key: string;
  model?: string;
  manufacturer?: string;
  os_version?: string;
  app_version?: string;
  app_build?: string;
}

export interface DeviceSelfRegisterResult {
  registered: boolean;
  device_key?: string;
  /** Present when registration was skipped or failed; login continues anyway. */
  reason?: string;
}

const STORAGE_KEY = 'jarvis_device_uid';

// Indirection so the plugin and the network can be replaced in tests without
// module mocking, and so this module has no import-time side effects.
type InfoProvider = () => Promise<{ version: string; build: string }>;
type HardwareProvider = () => Promise<{ model?: string; manufacturer?: string; osVersion?: string }>;
type Poster = (payload: DeviceRegistration) => Promise<void>;
type NativeCheck = () => boolean;

const defaults: {
  isNative: NativeCheck;
  getAppInfo: InfoProvider;
  getDeviceInfo: HardwareProvider;
  post: Poster;
} = {
  isNative: () => Capacitor.isNativePlatform(),
  getAppInfo: async () => {
    const { App } = await import('@capacitor/app');
    const info = await App.getInfo();
    return { version: info.version, build: info.build };
  },
  getDeviceInfo: async () => {
    const { Device } = await import('@capacitor/device');
    const info = await Device.getInfo();
    return { model: info.model, manufacturer: info.manufacturer, osVersion: info.osVersion };
  },
  post: async (payload) => {
    const axios = (await import('axios')).default;
    await axios.post(`${getServerOrigin()}/api/user-panel/devices/register`, payload, {
      timeout: 7000,
    });
  },
};

let impl = { ...defaults };

/** Swap the plugin/network seams. Returns a restore function for afterEach. */
export function __setImpl(next: Partial<typeof impl>): () => void {
  const previous = impl;
  impl = { ...impl, ...next };
  return () => {
    impl = previous;
  };
}

const isNative = (): boolean => impl.isNative();
const getAppInfo = (): Promise<{ version: string; build: string }> => impl.getAppInfo();
const getDeviceInfo = () => impl.getDeviceInfo();
const postRegistration = (payload: DeviceRegistration): Promise<void> => impl.post(payload);

function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: a new id is generated next time, so a device may
       re-register under a second row. Not worth failing a login over. */
  }
}

function randomId(): string {
  const bytes = new Uint8Array(16);
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i += 1) {
      bytes[i] = Math.floor(Math.random() * 256);
    }
  }
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

/** A stable id for this install, created on first use. */
export async function getDeviceKey(): Promise<string> {
  const key = resolveDeviceKey(readStored(STORAGE_KEY), randomId);
  if (key !== readStored(STORAGE_KEY)) writeStored(STORAGE_KEY, key);
  return key;
}

/**
 * Report this device to the server. Safe to call on every login: the server
 * upserts on `device_key`, so repeated calls update one row rather than
 * creating duplicates.
 *
 * Returns rather than throws, so a caller on the login path can ignore it.
 */
export async function registerThisDevice(): Promise<DeviceSelfRegisterResult> {
  // A browser is not a device in the registry; the panel is about hardware.
  if (!isNative()) return { registered: false, reason: 'not a native platform' };

  try {
    const deviceKey = await getDeviceKey();
    const payload: DeviceRegistration = { device_key: deviceKey };

    // App.getInfo() reports the running APK's own version; `build` is the
    // versionCode the update check compares against.
    try {
      const info = await getAppInfo();
      payload.app_version = info.version;
      payload.app_build = info.build;
    } catch {
      // No native shell to ask; model/os/app version stay unknown.
    }

    // Hardware details are optional: without them the panel cannot say which
    // phone this is, but registration is still worth doing.
    try {
      const hardware = await getDeviceInfo();
      payload.model = hardware.model;
      payload.manufacturer = hardware.manufacturer;
      payload.os_version = hardware.osVersion;
    } catch {
      // @capacitor/device is not installed.
    }

    await postRegistration(payload);
    return { registered: true, device_key: deviceKey };
  } catch (err) {
    // Deliberately swallowed: telemetry must never stand between a user and
    // their app. The console is the only place this surfaces.
    console.warn('[DeviceRegister] self-registration failed:', err);
    return { registered: false, reason: 'request failed' };
  }
}
