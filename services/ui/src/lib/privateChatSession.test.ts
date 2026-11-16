import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { chatLeft, chatShown, forgetLockAfter, getLockAfter, lockAll, lockNow, lockPending, pickerDone, pickerOpening, PICKER_GRACE_MS, setLockAfter, vaultFor } from './privateChatSession';

async function unlockedVault(token: string) {
  const vault = vaultFor(token);
  const key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
  vault.adopt(key);
  const picture = await vault.encrypt(new Uint8Array([1, 2, 3]));
  await vault.pictureUrl(picture, 'image/png');
  return vault;
}

const store = new Map<string, string>();

beforeEach(() => {
  store.clear();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  });
});

afterEach(() => {
  lockAll();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('private chat lock timing', () => {
  it('defaults to locking on leave, per chat, and refuses an unknown setting', () => {
    expect(getLockAfter('roomA')).toBe('leave');
    setLockAfter('roomA', '5m');
    expect(getLockAfter('roomA')).toBe('5m');
    expect(getLockAfter('roomB')).toBe('leave');
    expect(() => setLockAfter('roomA', 'never' as never)).toThrow();
    store.set('jarvis.privateChat.lockAfter.roomC', 'forever');
    expect(getLockAfter('roomC')).toBe('leave');
    forgetLockAfter('roomA');
    expect(getLockAfter('roomA')).toBe('leave');
  });

  it('locks at once on leave by default, wiping the key and every picture', async () => {
    const vault = await unlockedVault('room1');
    const revoke = vi.spyOn(URL, 'revokeObjectURL');
    chatLeft('room1');
    expect(vault.unlocked).toBe(false);
    expect(vault.liveUrls).toBe(0);
    expect(revoke).toHaveBeenCalled();
  });

  it('waits for the timer, releases pictures at once, and a return cancels it', async () => {
    const vault = await unlockedVault('room2');
    vi.useFakeTimers();
    setLockAfter('room2', '1m');
    chatLeft('room2');
    // Pictures go the moment the chat is off the screen; the key stays.
    expect(vault.liveUrls).toBe(0);
    expect(vault.unlocked).toBe(true);
    expect(lockPending('room2')).toBe(true);
    vi.advanceTimersByTime(59_000);
    chatShown('room2');
    expect(lockPending('room2')).toBe(false);
    vi.advanceTimersByTime(10 * 60_000);
    expect(vault.unlocked).toBe(true);

    chatLeft('room2');
    vi.advanceTimersByTime(60_000);
    expect(vault.unlocked).toBe(false);
    await expect(vault.open('x')).rejects.toThrow(/locked/);
  });

  it('15 minutes means 15 minutes', async () => {
    const vault = await unlockedVault('room3');
    vi.useFakeTimers();
    setLockAfter('room3', '15m');
    chatLeft('room3');
    vi.advanceTimersByTime(15 * 60_000 - 1);
    expect(vault.unlocked).toBe(true);
    vi.advanceTimersByTime(1);
    expect(vault.unlocked).toBe(false);
  });

  it('"only when Jarvis is closed" never locks on a timer, but Lock and signing out still do', async () => {
    const vault = await unlockedVault('room4');
    vi.useFakeTimers();
    setLockAfter('room4', 'close');
    chatLeft('room4');
    vi.advanceTimersByTime(24 * 60 * 60_000);
    expect(vault.unlocked).toBe(true);
    lockNow('room4');
    expect(vault.unlocked).toBe(false);

    const other = await unlockedVault('room5');
    setLockAfter('room5', 'close');
    chatLeft('room5');
    lockAll();
    expect(other.unlocked).toBe(false);
  });

  it('keeps one vault per chat for the life of the app', () => {
    expect(vaultFor('room6')).toBe(vaultFor('room6'));
    expect(vaultFor('room6')).not.toBe(vaultFor('room7'));
  });

  it('the phone picker opening over the chat does not lock it until the grace ends, and a return cancels it', async () => {
    const vault = await unlockedVault('room9');
    vi.useFakeTimers();
    pickerOpening('room9');
    chatLeft('room9');
    // Pictures go at once as always; the key waits for the pictures to be chosen.
    expect(vault.liveUrls).toBe(0);
    expect(vault.unlocked).toBe(true);
    vi.advanceTimersByTime(PICKER_GRACE_MS - 1000);
    chatShown('room9');
    vi.advanceTimersByTime(10 * 60_000);
    expect(vault.unlocked).toBe(true);
    // The hold is used once: the next leave locks at once again.
    chatLeft('room9');
    expect(vault.unlocked).toBe(false);
  });

  it('a picker left open past the grace locks the chat, and a closed picker gives no grace', async () => {
    const vault = await unlockedVault('room10');
    vi.useFakeTimers();
    pickerOpening('room10');
    chatLeft('room10');
    vi.advanceTimersByTime(PICKER_GRACE_MS);
    expect(vault.unlocked).toBe(false);

    const again = await unlockedVault('room11');
    pickerOpening('room11');
    pickerDone('room11');
    chatLeft('room11');
    expect(again.unlocked).toBe(false);
  });
});
