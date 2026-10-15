import { Capacitor } from '@capacitor/core';
import { Preferences } from '@capacitor/preferences';
import TokenBridge from '../plugins/tokenBridge';

const isNative = Capacitor.isNativePlatform();
const _cache: Record<string, string | null> = {};

/** Credentials the native side (location service, widgets, chat notifier) reads. */
const NATIVE_KEYS = new Set(['jarvis_api_key', 'jarvis_server_url']);

/** Hand the credentials to TokenBridge; `''` clears one, `undefined` leaves it. */
async function pushNative(apiKey: string | undefined, serverUrl: string | undefined): Promise<void> {
  try {
    await TokenBridge.setCredentials({ apiKey, serverUrl });
    await TokenBridge.refreshWidgets();
  } catch (err) {
    // Never fail silent credential handoff to native widgets
    console.error('[storage] TokenBridge credential sync failed:', err);
  }
}

export async function storageInit(): Promise<void> {
  if (isNative) {
    const { value: apiKey } = await Preferences.get({ key: 'jarvis_api_key' });
    let { value: serverUrl } = await Preferences.get({ key: 'jarvis_server_url' });
    if (!serverUrl) {
      serverUrl = 'https://jarvis.sumemail.com';
      await Preferences.set({ key: 'jarvis_server_url', value: serverUrl });
    }
    // Clients authenticate with their own API key only. Older builds could
    // keep the services' internal secret here and send it on every request;
    // drop any copy left on the phone.
    await Preferences.remove({ key: 'internal_secret' });
    _cache['jarvis_api_key'] = apiKey;
    _cache['jarvis_server_url'] = serverUrl;
    await pushNative(apiKey ?? undefined, serverUrl ?? undefined);
  }
}

export function storageGetSync(key: string): string | null {
  if (isNative) {
    return _cache[key] ?? null;
  }
  return localStorage.getItem(key);
}

export async function storageGet(key: string): Promise<string | null> {
  if (isNative) {
    const { value } = await Preferences.get({ key });
    _cache[key] = value;
    return value;
  }
  return localStorage.getItem(key);
}

export async function storageSet(key: string, value: string): Promise<void> {
  _cache[key] = value;
  if (isNative) {
    await Preferences.set({ key, value });
    if (NATIVE_KEYS.has(key)) {
      await pushNative(key === 'jarvis_api_key' ? value : undefined, key === 'jarvis_server_url' ? value : undefined);
    }
  } else {
    localStorage.setItem(key, value);
  }
}

/**
 * Copy the stored credentials to the native side (TokenBridge), where the
 * background location service and the home-screen widgets read them.
 *
 * storageSet does this only when a key is written, i.e. on an explicit login.
 * A phone that stayed signed in from before those native readers existed
 * never wrote them again, so the location service found no credentials and
 * uploaded nothing. Called on every session restore.
 */
export async function mirrorNativeCredentials(): Promise<void> {
  if (!isNative) return;
  const apiKey = _cache['jarvis_api_key'] ?? (await Preferences.get({ key: 'jarvis_api_key' })).value ?? undefined;
  const serverUrl = _cache['jarvis_server_url'] ?? (await Preferences.get({ key: 'jarvis_server_url' })).value ?? undefined;
  if (!apiKey && !serverUrl) return;
  await pushNative(apiKey || undefined, serverUrl || undefined);
}

export async function storageRemove(key: string): Promise<void> {
  delete _cache[key];
  if (isNative) {
    await Preferences.remove({ key });
    if (NATIVE_KEYS.has(key)) {
      await pushNative(key === 'jarvis_api_key' ? '' : undefined, key === 'jarvis_server_url' ? '' : undefined);
    }
  } else {
    localStorage.removeItem(key);
  }
}

export async function storageClear(): Promise<void> {
  for (const k of Object.keys(_cache)) delete _cache[k];
  if (isNative) {
    await Preferences.clear();
  } else {
    localStorage.clear();
  }
}
