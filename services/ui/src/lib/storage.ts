import { Capacitor } from '@capacitor/core';
import { Preferences } from '@capacitor/preferences';

const isNative = Capacitor.isNativePlatform();
const _cache: Record<string, string | null> = {};

export async function storageInit(): Promise<void> {
  if (isNative) {
    const { value: apiKey } = await Preferences.get({ key: 'jarvis_api_key' });
    const { value: internalSecret } = await Preferences.get({ key: 'internal_secret' });
    const { value: serverUrl } = await Preferences.get({ key: 'jarvis_server_url' });
    _cache['jarvis_api_key'] = apiKey;
    _cache['internal_secret'] = internalSecret;
    _cache['jarvis_server_url'] = serverUrl;
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
  } else {
    localStorage.setItem(key, value);
  }
}
}

export async function storageRemove(key: string): Promise<void> {
  delete _cache[key];
  if (isNative) {
    await Preferences.remove({ key });
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
