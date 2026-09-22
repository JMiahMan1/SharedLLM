import { Capacitor } from '@capacitor/core';
import { Preferences } from '@capacitor/preferences';
import TokenBridge from '../plugins/tokenBridge';

const isNative = Capacitor.isNativePlatform();
const _cache: Record<string, string | null> = {};

export async function storageInit(): Promise<void> {
  if (isNative) {
    const { value: apiKey } = await Preferences.get({ key: 'jarvis_api_key' });
    const { value: internalSecret } = await Preferences.get({ key: 'internal_secret' });
    let { value: serverUrl } = await Preferences.get({ key: 'jarvis_server_url' });
    if (!serverUrl) {
      serverUrl = 'https://jarvis.sumemail.com';
      await Preferences.set({ key: 'jarvis_server_url', value: serverUrl });
    }
    _cache['jarvis_api_key'] = apiKey;
    _cache['internal_secret'] = internalSecret;
    _cache['jarvis_server_url'] = serverUrl;
    TokenBridge.setCredentials({
      apiKey: apiKey ?? undefined,
      serverUrl: serverUrl ?? undefined,
    }).catch(() => undefined);
    TokenBridge.refreshWidgets().catch(() => undefined);
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
    if (key === 'jarvis_api_key' || key === 'jarvis_server_url') {
      try {
        await TokenBridge.setCredentials({
          apiKey: key === 'jarvis_api_key' ? value : undefined,
          serverUrl: key === 'jarvis_server_url' ? value : undefined,
        });
        await TokenBridge.refreshWidgets();
      } catch {
        // native bridge unavailable
      }
    }
  } else {
    localStorage.setItem(key, value);
  }
}

export async function storageRemove(key: string): Promise<void> {
  delete _cache[key];
  if (isNative) {
    await Preferences.remove({ key });
    if (key === 'jarvis_api_key' || key === 'jarvis_server_url') {
      try {
        await TokenBridge.setCredentials({
          apiKey: key === 'jarvis_api_key' ? '' : undefined,
          serverUrl: key === 'jarvis_server_url' ? '' : undefined,
        });
        await TokenBridge.refreshWidgets();
      } catch {
        // native bridge unavailable
      }
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
