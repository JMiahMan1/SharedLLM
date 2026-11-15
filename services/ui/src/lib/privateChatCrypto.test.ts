import { describe, expect, it, vi } from 'vitest';
import {
  checkVerifier,
  decryptBytes,
  deriveKey,
  encryptBytes,
  fromBase64,
  isSealedMessage,
  newChatSecrets,
  openMessage,
  PBKDF2_ITERATIONS,
  PrivateVault,
  readSealed,
  sealMessage,
  SEALED_LINE,
} from './privateChatCrypto';

// The real stretch (600,000 rounds) runs once per describe block; it is the
// point of the design, so the tests use it rather than a weaker count.
describe('private chat crypto', () => {
  it('derives the same key from the same password on the other parent\'s device', async () => {
    const created = await newChatSecrets('correct horse battery');
    expect(created.iterations).toBe(PBKDF2_ITERATIONS);
    expect(fromBase64(created.salt)).toHaveLength(16);
    expect(fromBase64(created.verifier.iv)).toHaveLength(12);

    const other = await deriveKey('correct horse battery', { salt: created.salt, iterations: created.iterations });
    expect(await checkVerifier(other, created.verifier)).toBe(true);

    const wire = await sealMessage(created.key, { t: 'text', text: 'Date night Friday?' });
    expect(wire.startsWith(SEALED_LINE)).toBe(true);
    expect(wire).not.toContain('Date night');
    expect(await openMessage(other, wire)).toEqual({ t: 'text', text: 'Date night Friday?' });
  }, 30_000);

  it('rejects a wrong password through the verifier and cannot open messages with it', async () => {
    const created = await newChatSecrets('the real one');
    const wrong = await deriveKey('not the real one', { salt: created.salt, iterations: created.iterations });
    expect(await checkVerifier(wrong, created.verifier)).toBe(false);
    const wire = await sealMessage(created.key, { t: 'text', text: 'secret' });
    expect(await openMessage(wrong, wire)).toBeNull();
  }, 30_000);

  it('uses a fresh IV for every message and refuses a tampered one', async () => {
    const { key } = await newChatSecrets('pw');
    const a = readSealed(await sealMessage(key, { t: 'text', text: 'same' }))!;
    const b = readSealed(await sealMessage(key, { t: 'text', text: 'same' }))!;
    expect(a.iv).not.toBe(b.iv);
    expect(a.ct).not.toBe(b.ct);
    const bytes = fromBase64(a.ct);
    bytes[0] ^= 1;
    const tampered = `x\n\n\`\`\`jarvis-envelope\n${JSON.stringify({ kind: 'sealed', iv: a.iv, ct: btoa(String.fromCharCode(...bytes)) })}\n\`\`\``;
    expect(await openMessage(key, tampered)).toBeNull();
    expect(isSealedMessage('hello')).toBe(false);
    expect(await openMessage(key, 'hello')).toBeNull();
  }, 30_000);

  it('round-trips a picture and refuses one too short to be ciphertext', async () => {
    const { key } = await newChatSecrets('pw');
    const picture = new Uint8Array(4096).map((_, i) => i % 251);
    const sealed = await encryptBytes(key, picture);
    expect(sealed.length).toBe(picture.length + 12 + 16);
    expect(await decryptBytes(key, sealed)).toEqual(picture);
    await expect(decryptBytes(key, sealed.slice(0, 8))).rejects.toThrow();
  }, 30_000);

  it('never accepts a weaker stretch than the minimum', async () => {
    await expect(deriveKey('pw', { salt: 'AAAAAAAAAAAAAAAAAAAAAA==', iterations: 1000 })).rejects.toThrow(/too weak/);
  });
});

describe('PrivateVault', () => {
  it('unlocks only with the right password and forgets everything on lock', async () => {
    const created = await newChatSecrets('pw-1');
    const params = { salt: created.salt, iterations: created.iterations };
    const vault = new PrivateVault();
    const changes = vi.fn();
    vault.subscribe(changes);

    expect(await vault.unlock('nope', params, created.verifier)).toBe(false);
    expect(vault.unlocked).toBe(false);
    expect(await vault.unlock('pw-1', params, created.verifier)).toBe(true);
    expect(vault.unlocked).toBe(true);

    const wire = await vault.seal({ t: 'text', text: 'hi' });
    expect(await vault.open(wire)).toEqual({ t: 'text', text: 'hi' });

    const revoke = vi.spyOn(URL, 'revokeObjectURL');
    const picture = await vault.encrypt(new Uint8Array([1, 2, 3, 4]));
    const url = await vault.pictureUrl(picture, 'image/png');
    expect(vault.liveUrls).toBe(1);

    vault.lock();
    expect(vault.unlocked).toBe(false);
    expect(vault.liveUrls).toBe(0);
    expect(revoke).toHaveBeenCalledWith(url);
    await expect(vault.open(wire)).rejects.toThrow(/locked/);
    await expect(vault.seal({ t: 'text', text: 'x' })).rejects.toThrow(/locked/);
    expect(changes).toHaveBeenCalledTimes(2);
  }, 60_000);
});
