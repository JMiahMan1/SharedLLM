import { afterAll, beforeAll, vi } from 'vitest';

/**
 * Remember PBKDF2 results for the rest of a test file.
 *
 * A chat password goes through PBKDF2 at 600,000 iterations or more (the
 * decryptor refuses fewer), and a file that opens the same backup a dozen
 * times paid that cost every time: a few seconds each on a busy machine, so
 * a test that opened a backup seven times ran past its time limit in a full
 * run and passed alone.
 *
 * PBKDF2 is a pure function of the password, salt, hash, iteration count and
 * output, so the first call for each input runs the real derivation and later
 * ones reuse it. A wrong password is a different input and is still derived.
 * Everything after the derivation (unwrapping, verifiers, decryption) runs
 * for real on every call.
 */
export function memoizePbkdf2(): void {
  const subtle = globalThis.crypto.subtle;
  const passwordOf = new WeakMap<CryptoKey, string>();
  const bits = new Map<string, Promise<ArrayBuffer>>();
  const keys = new Map<string, Promise<CryptoKey>>();
  const spies: Array<{ mockRestore: () => void }> = [];

  const hex = (data: BufferSource) =>
    Buffer.from(ArrayBuffer.isView(data) ? new Uint8Array(data.buffer, data.byteOffset, data.byteLength) : new Uint8Array(data)).toString('hex');

  /** The cache key for a PBKDF2 call, or null for anything else. */
  const pbkdf2Key = (algorithm: AlgorithmIdentifier | Pbkdf2Params, base: CryptoKey, rest: string): string | null => {
    if (typeof algorithm === 'string' || algorithm.name !== 'PBKDF2') return null;
    const password = passwordOf.get(base);
    if (password === undefined) return null;
    const params = algorithm as Pbkdf2Params;
    const hash = typeof params.hash === 'string' ? params.hash : params.hash.name;
    return [password, hex(params.salt), hash, params.iterations, rest].join('|');
  };

  beforeAll(() => {
    const importKey = subtle.importKey.bind(subtle);
    const deriveBits = subtle.deriveBits.bind(subtle);
    const deriveKey = subtle.deriveKey.bind(subtle);

    spies.push(vi.spyOn(subtle, 'importKey').mockImplementation((async (...args: Parameters<SubtleCrypto['importKey']>) => {
      const key = await (importKey as (...a: unknown[]) => Promise<CryptoKey>)(...args);
      const [format, data, algorithm] = args;
      const name = typeof algorithm === 'string' ? algorithm : algorithm.name;
      if (format === 'raw' && name === 'PBKDF2') passwordOf.set(key, hex(data as BufferSource));
      return key;
    }) as SubtleCrypto['importKey']));

    spies.push(vi.spyOn(subtle, 'deriveBits').mockImplementation(async (algorithm, base, length) => {
      const id = pbkdf2Key(algorithm as Pbkdf2Params, base, String(length));
      if (id === null) return deriveBits(algorithm, base, length);
      if (!bits.has(id)) bits.set(id, deriveBits(algorithm, base, length));
      // A copy: the caller zeroes the bits once it has used them.
      return (await bits.get(id)!).slice(0);
    }));

    spies.push(vi.spyOn(subtle, 'deriveKey').mockImplementation(async (algorithm, base, derived, extractable, usages) => {
      const id = pbkdf2Key(algorithm as Pbkdf2Params, base, JSON.stringify([derived, extractable, [...usages]]));
      if (id === null) return deriveKey(algorithm, base, derived, extractable, usages);
      if (!keys.has(id)) keys.set(id, deriveKey(algorithm, base, derived, extractable, usages));
      return keys.get(id)!;
    }));
  });

  afterAll(() => {
    for (const spy of spies) spy.mockRestore();
    spies.length = 0;
  });
}
