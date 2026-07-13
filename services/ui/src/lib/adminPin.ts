/**
 * Admin PIN store for the mobile app's admin elevation gate.
 *
 * The PIN is hashed (SHA-256 with a static salt) before being persisted to
 * localStorage so it is never stored in plaintext. On first run no PIN is set,
 * so the Settings screen is where an admin configures one.
 */

const ADMIN_PIN_KEY = 'jarvis_admin_pin_hash';
const ADMIN_PIN_SALT = 'jarvis-admin-pin-v1';

function storageGetSync(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function storageSetSync(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}

function storageRemoveSync(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

async function sha256Hex(input: string): Promise<string> {
  const data = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

export function getAdminPinHash(): string | null {
  return storageGetSync(ADMIN_PIN_KEY);
}

export function isAdminPinSet(): boolean {
  return Boolean(getAdminPinHash());
}

export async function setAdminPin(pin: string): Promise<void> {
  storageSetSync(ADMIN_PIN_KEY, await sha256Hex(ADMIN_PIN_SALT + pin));
}

export function clearAdminPin(): void {
  storageRemoveSync(ADMIN_PIN_KEY);
}

export async function verifyAdminPin(pin: string): Promise<boolean> {
  const stored = getAdminPinHash();
  if (!stored) return false;
  return (await sha256Hex(ADMIN_PIN_SALT + pin)) === stored;
}
