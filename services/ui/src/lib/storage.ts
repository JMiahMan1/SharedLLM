import { Capacitor } from '@capacitor/core';
import { Preferences } from '@capacitor/preferences';

const isNative = Capacitor.isNativePlatform();

let _cache: Record<string, string | null> = {};

export async function storageInit(keys: string[]): Promise<void> {
  if (isNative) {
    const entries = await Preferences.get({ keys });
    for (const key of keys) {
      _cache[key] = entries[key] ?? null;
    }
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

export async function storageRemove(key: string): Promise<void> {
  delete _cache[key];
  if (isNative) {
    await Preferences.remove({ key });
  } else {
    localStorage.removeItem(key);
  }
}

export async function storageClear(): Promise<void> {
  _cache = {};
  if (isNative) {
    await Preferences.clear();
  } else {
    localStorage.clear();
  }
}
