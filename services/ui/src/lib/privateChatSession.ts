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
/** Until when (ms) leaving a chat is the phone's own file picker opening over it. */
const pickers = new Map<string, number>();

/** How long the phone's picker may cover the chat before it locks anyway. */
export const PICKER_GRACE_MS = 3 * 60_000;

/**
 * The phone's file picker is about to open over the chat. On Android that
 * sends the app to the background, which would lock the chat at once and
 * leave the chosen pictures with no key to encrypt them. The next leave
 * within the grace only releases pictures and locks when the grace ends
 * (or later, by this device's choice), unless the chat is shown again first.
 */
export function pickerOpening(token: string, graceMs: number = PICKER_GRACE_MS): void {
  pickers.set(token, Date.now() + graceMs);
  // The window has focus again when the picker closes (chosen or cancelled): from then on leaving locks as chosen.
  // On Android that is after the app came back, which already cleared the hold.
  if (typeof window !== 'undefined') {
    window.addEventListener('blur', () => window.addEventListener('focus', () => pickerDone(token), { once: true }), { once: true });
  }
}

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
  pickers.delete(token);
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
  const until = pickers.get(token);
  pickers.delete(token);
  const grace = until !== undefined ? until - Date.now() : 0;
  const ms = option.ms === null ? null : Math.max(option.ms, grace);
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
  pickers.delete(token);
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

/** The picker closed (something was chosen, or it was cancelled): leaving locks as chosen again. */
export function pickerDone(token: string): void {
  pickers.delete(token);
}
