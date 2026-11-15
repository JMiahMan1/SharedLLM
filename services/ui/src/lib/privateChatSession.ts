/**
 * How long an unlocked private chat stays unlocked once it is off the screen.
 *
 * A per-device, per-chat choice kept in this app's local storage (never on the
 * server): lock when I leave the chat (the default), after 1, 5 or 15 minutes,
 * or only when Jarvis is closed. Leaving the chat or sending the app to the
 * background starts the timer; coming back before it fires cancels it. The
 * key itself lives only in memory (`PrivateVault`), so a closed or killed app
 * is always locked. When the timer fires the vault is wiped exactly as a
 * manual lock wipes it.
 */
import { PrivateVault } from './privateChatCrypto';

export type LockAfter = 'leave' | '1m' | '5m' | '15m' | 'close';

export const LOCK_OPTIONS: ReadonlyArray<{ value: LockAfter; label: string; ms: number | null }> = [
  { value: 'leave', label: 'When I leave the chat', ms: 0 },
  { value: '1m', label: 'After 1 minute', ms: 60_000 },
  { value: '5m', label: 'After 5 minutes', ms: 5 * 60_000 },
  { value: '15m', label: 'After 15 minutes', ms: 15 * 60_000 },
  { value: 'close', label: 'Only when Jarvis is closed', ms: null },
];

const STORAGE_PREFIX = 'jarvis.privateChat.lockAfter.';

function isLockAfter(value: unknown): value is LockAfter {
  return LOCK_OPTIONS.some((o) => o.value === value);
}

/** This device's choice for a chat; "when I leave the chat" unless changed. */
export function getLockAfter(token: string): LockAfter {
  try {
    const value = globalThis.localStorage?.getItem(STORAGE_PREFIX + token);
    return isLockAfter(value) ? value : 'leave';
  } catch {
    return 'leave';
  }
}

export function setLockAfter(token: string, value: LockAfter): void {
  if (!isLockAfter(value)) throw new Error(`Unknown lock setting: ${String(value)}`);
  globalThis.localStorage.setItem(STORAGE_PREFIX + token, value);
}

export function forgetLockAfter(token: string): void {
  try {
    globalThis.localStorage?.removeItem(STORAGE_PREFIX + token);
  } catch {
    // Nothing kept.
  }
}

const vaults = new Map<string, PrivateVault>();
const timers = new Map<string, ReturnType<typeof setTimeout>>();

/** The one vault for a chat in this app's memory (it outlives the chat screen, never the app). */
export function vaultFor(token: string): PrivateVault {
  let vault = vaults.get(token);
  if (!vault) {
    vault = new PrivateVault();
    vaults.set(token, vault);
  }
  return vault;
}

function cancel(token: string): void {
  const timer = timers.get(token);
  if (timer !== undefined) clearTimeout(timer);
  timers.delete(token);
}

/** The chat is on screen again: a pending lock is cancelled. */
export function chatShown(token: string): void {
  cancel(token);
}

/**
 * The chat went off the screen (left, or the app went to the background).
 * Decrypted pictures are always released at once; the key goes now, later or
 * when the app closes, by this device's choice.
 */
export function chatLeft(token: string, lockAfter: LockAfter = getLockAfter(token)): void {
  const vault = vaults.get(token);
  if (!vault) return;
  vault.releasePictures();
  cancel(token);
  const option = LOCK_OPTIONS.find((o) => o.value === lockAfter) ?? LOCK_OPTIONS[0];
  const ms = option.ms;
  if (ms === null || !vault.unlocked) return;
  if (ms === 0) {
    vault.lock();
    return;
  }
  timers.set(
    token,
    setTimeout(() => {
      timers.delete(token);
      vault.lock();
    }, ms),
  );
}

/** Lock a chat now (the Lock button, a deleted chat). */
export function lockNow(token: string): void {
  cancel(token);
  vaults.get(token)?.lock();
}

/** Lock every chat (signing out). */
export function lockAll(): void {
  for (const token of [...vaults.keys()]) lockNow(token);
}

/** Whether a chat has a lock waiting to fire (for tests and the settings copy). */
export function lockPending(token: string): boolean {
  return timers.has(token);
}
