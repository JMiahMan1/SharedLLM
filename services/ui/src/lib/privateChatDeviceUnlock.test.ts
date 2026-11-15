import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DeviceUnlockPluginInterface } from '../plugins/deviceUnlock';
import { deviceUnlockAvailable, deviceUnlockPlugin } from '../plugins/deviceUnlock';
import { enableDeviceUnlock, keyFromDevice } from './privateChatDeviceUnlock';
import {
  deriveKey,
  fromBase64,
  makeVerifier,
  openMessage,
  PBKDF2_ITERATIONS,
  randomBytes,
  sealMessage,
  toBase64,
  type ChatKeyInfo,
} from './privateChatCrypto';

async function v1Chat(password: string): Promise<{ info: ChatKeyInfo; key: CryptoKey }> {
  const kdf = { salt: toBase64(randomBytes(16)), iterations: PBKDF2_ITERATIONS };
  const key = await deriveKey(password, kdf);
  return { info: { version: 1, kdf, verifier: await makeVerifier(key) }, key };
}

/** A stand-in for the Android plugin that keeps what it is given (as the Keystore would, encrypted). */
function fakePlugin(): DeviceUnlockPluginInterface & { stored: Map<string, string> } {
  const stored = new Map<string, string>();
  return {
    stored,
    available: vi.fn(async () => ({ available: true })),
    isEnabled: vi.fn(async ({ token }) => ({ enabled: stored.has(token) })),
    enable: vi.fn(async ({ token, key }) => {
      stored.set(token, key);
      return { enabled: true };
    }),
    unlock: vi.fn(async ({ token }) => {
      const key = stored.get(token);
      if (!key) throw new Error('Not set up');
      return { key };
    }),
    disable: vi.fn(async ({ token }) => {
      stored.delete(token);
      return { enabled: false };
    }),
  };
}

afterEach(() => vi.restoreAllMocks());

describe('unlock with the phone lock (page side)', () => {
  it('turns on only with the right password, hands over the data key, and zeroes it', async () => {
    const { info, key } = await v1Chat('the chat password');
    const plugin = fakePlugin();
    await expect(enableDeviceUnlock(plugin, 'roomA', 'wrong password', info)).rejects.toThrow(/not the password/);
    expect(plugin.enable).not.toHaveBeenCalled();

    const subtle = globalThis.crypto.subtle;
    const original = subtle.deriveBits.bind(subtle);
    const seen: ArrayBuffer[] = [];
    vi.spyOn(subtle, 'deriveBits').mockImplementation((async (...args: Parameters<SubtleCrypto['deriveBits']>) => {
      const out = await original(...args);
      seen.push(out);
      return out;
    }) as never);
    await enableDeviceUnlock(plugin, 'roomA', 'the chat password', info);
    expect(seen).toHaveLength(1);
    expect(new Uint8Array(seen[0]).every((b) => b === 0)).toBe(true);
    expect(fromBase64(plugin.stored.get('roomA')!)).toHaveLength(32);

    // What the phone gives back opens the chat, as a key that can never be exported.
    const back = await keyFromDevice(plugin, 'roomA', info.verifier);
    expect(back.extractable).toBe(false);
    const wire = await sealMessage(key, { t: 'text', text: 'hello' });
    expect(await openMessage(back, wire)).toEqual({ t: 'text', text: 'hello' });
  }, 60_000);

  it('zeroes the bytes it imports', async () => {
    const { info } = await v1Chat('pw pw pw pw');
    const plugin = fakePlugin();
    await enableDeviceUnlock(plugin, 'roomB', 'pw pw pw pw', info);
    const subtle = globalThis.crypto.subtle;
    const original = subtle.importKey.bind(subtle);
    const given: Uint8Array[] = [];
    vi.spyOn(subtle, 'importKey').mockImplementation((async (...args: Parameters<SubtleCrypto['importKey']>) => {
      if (args[0] === 'raw' && args[1] instanceof Uint8Array) given.push(args[1]);
      return original(...args);
    }) as never);
    await keyFromDevice(plugin, 'roomB', info.verifier);
    expect(given).toHaveLength(1);
    expect(given[0].every((b) => b === 0)).toBe(true);
  }, 60_000);

  it('forgets a phone key that does not open the chat, and says to use the password', async () => {
    const { info } = await v1Chat('pw pw pw pw');
    const plugin = fakePlugin();
    plugin.stored.set('roomC', toBase64(randomBytes(32)));
    await expect(keyFromDevice(plugin, 'roomC', info.verifier)).rejects.toThrow(/Use the password/);
    expect(plugin.disable).toHaveBeenCalledWith({ token: 'roomC' });
  }, 60_000);

  it('passes a plugin refusal through (cancelled, or a changed phone lock)', async () => {
    const { info } = await v1Chat('pw pw pw pw');
    const plugin = fakePlugin();
    vi.mocked(plugin.unlock).mockRejectedValueOnce(new Error('Set it up again with the password.'));
    await expect(keyFromDevice(plugin, 'roomD', info.verifier)).rejects.toThrow(/again with the password/);
  }, 60_000);

  it('is never offered in a browser (no plugin, no stand-in storage)', async () => {
    expect(deviceUnlockPlugin()).toBeNull();
    expect(await deviceUnlockAvailable()).toBe(false);
    expect(await deviceUnlockAvailable(null)).toBe(false);
  });
});
