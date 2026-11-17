import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  changePasswordRequest,
  deriveKey,
  fromBase64,
  makeVerifier,
  newChatSecrets,
  PBKDF2_ITERATIONS,
  PrivateVault,
  randomBytes,
  readSealed,
  toBase64,
  type ChatKeyInfo,
} from './privateChatCrypto';
import {
  BACKUP_KEY_RECORD,
  configureBackupKeys,
  ensureBackupKey,
  generateBackupKeyPair,
  makeBackupKey,
  openBackupKey,
  resetBackupKeys,
  watchVaultForBackupKey,
  type BackupKeyClient,
  type BackupKeyUpload,
} from './privateChatBackupKey';

/**
 * Test vectors the Python side (scripts/private_chat_backup.py and
 * scripts/open_private_chat_backup.py) opens: chats made with this file's
 * crypto, so the two implementations are checked against each other.
 * Regenerate with WRITE_BACKUP_VECTORS=1 npx vitest run src/lib/privateChatBackupKey.test.ts
 */
const VECTORS = resolve(__dirname, '../../../../tests/unit/fixtures/private_chat_backup_vectors.json');

/** Fake passwords for fake chats. */
const FIRST = 'test password one';
const SECOND = 'test password two';
const OLD = 'test password v1';

async function keyed(): Promise<PrivateVault> {
  const vault = new PrivateVault();
  vault.adopt((await newChatSecrets(FIRST)).key);
  return vault;
}

afterEach(() => {
  configureBackupKeys(null);
  resetBackupKeys();
  vi.restoreAllMocks();
});

describe('the backup key pair', () => {
  it('is X25519 where the platform has it, and seals a private key only the chat key opens', async () => {
    const vault = await keyed();
    const upload = await makeBackupKey(vault, 1_700_000_000);
    expect(upload.alg).toBe('X25519');
    expect(fromBase64(upload.public_key)).toHaveLength(32);
    expect(readSealed(upload.sealed_private_key)).not.toBeNull();
    // Nothing readable in what goes to the server.
    expect(upload.sealed_private_key).not.toContain('private_key');
    const record = await openBackupKey(vault, upload.sealed_private_key);
    expect(record).toMatchObject({ t: BACKUP_KEY_RECORD, alg: 'X25519', public_key: upload.public_key, created_at: 1_700_000_000 });
    // The sealed private key really is the public key's other half.
    const priv = await crypto.subtle.importKey('pkcs8', fromBase64(record!.private_key), { name: 'X25519' }, true, ['deriveBits']);
    const jwk = await crypto.subtle.exportKey('jwk', priv);
    expect(Buffer.from(jwk.x!, 'base64url').toString('base64')).toBe(upload.public_key);

    const other = await keyed();
    expect(await openBackupKey(other, upload.sealed_private_key)).toBeNull();
  });

  it('falls back to P-256 when the WebView has no X25519', async () => {
    const real = crypto.subtle.generateKey.bind(crypto.subtle);
    vi.spyOn(crypto.subtle, 'generateKey').mockImplementation(((alg: AlgorithmIdentifier, ...rest: unknown[]) => {
      if ((alg as Algorithm).name === 'X25519') return Promise.reject(new DOMException('no', 'NotSupportedError'));
      return (real as (...args: unknown[]) => Promise<unknown>)(alg, ...rest);
    }) as typeof crypto.subtle.generateKey);
    const { alg } = await generateBackupKeyPair();
    expect(alg).toBe('P-256');
    const upload = await makeBackupKey(await keyed());
    expect(upload.alg).toBe('P-256');
    expect(fromBase64(upload.public_key)).toHaveLength(65);
  });
});

describe('making the key on unlock', () => {
  function fakeClient(existing: boolean, putStatus?: number) {
    const puts: BackupKeyUpload[] = [];
    const client: BackupKeyClient = {
      getChat: vi.fn(async () => ({ backup_key: existing ? { alg: 'X25519' as const, public_key: 'x', key_id: 'k', created_at: 1 } : null })),
      putBackupKey: vi.fn(async (_token: string, body: BackupKeyUpload) => {
        puts.push(body);
        if (putStatus) throw Object.assign(new Error('refused'), { response: { status: putStatus } });
        return {};
      }),
    };
    return { client, puts };
  }

  it('uploads one key the first time a chat without one is unlocked, and only once', async () => {
    const { client, puts } = fakeClient(false);
    configureBackupKeys(client);
    const vault = await keyed();
    expect(await ensureBackupKey('chat1', vault)).toBe('made');
    expect(await ensureBackupKey('chat1', vault)).toBe('skipped');
    expect(puts).toHaveLength(1);
  });

  it('leaves an existing key alone', async () => {
    const { client, puts } = fakeClient(true);
    configureBackupKeys(client);
    expect(await ensureBackupKey('chat1', await keyed())).toBe('exists');
    expect(puts).toHaveLength(0);
  });

  it('takes a 409 (the other phone was first) as done', async () => {
    const { client } = fakeClient(false, 409);
    configureBackupKeys(client);
    expect(await ensureBackupKey('chat1', await keyed())).toBe('raced');
  });

  it('does nothing while locked, and tries again on a later unlock after an error', async () => {
    const { client, puts } = fakeClient(false, 500);
    configureBackupKeys(client);
    const vault = new PrivateVault();
    expect(await ensureBackupKey('chat1', vault)).toBe('skipped');
    vault.adopt((await newChatSecrets(FIRST)).key);
    await expect(ensureBackupKey('chat1', vault)).rejects.toThrow('refused');
    await expect(ensureBackupKey('chat1', vault)).rejects.toThrow('refused');
    expect(puts).toHaveLength(2);
  });

  it('watching a vault makes the key as soon as any screen unlocks it', async () => {
    const { client, puts } = fakeClient(false);
    configureBackupKeys(client);
    const vault = new PrivateVault();
    const stop = watchVaultForBackupKey('chat2', vault);
    expect(client.getChat).not.toHaveBeenCalled();
    vault.adopt((await newChatSecrets(FIRST)).key);
    await vi.waitFor(() => expect(puts).toHaveLength(1));
    stop();
  });
});

describe('vectors for the Python backup tools', () => {
  it('opens the committed vectors with this crypto (and writes them when asked)', async () => {
    if (process.env.WRITE_BACKUP_VECTORS === '1' || !existsSync(VECTORS)) {
      // A v2 chat whose password changed once (its history keeps the first record), with an X25519 backup key.
      const made = await newChatSecrets(FIRST);
      const first: ChatKeyInfo = { version: 2, kdf: made.kdf, wrapped_key: made.wrapped_key, verifier: made.verifier };
      const change = await changePasswordRequest(first, FIRST, SECOND);
      const v2 = new PrivateVault();
      v2.adopt(made.key);
      const x = await makeBackupKey(v2, 1_700_000_000);
      // A v1 chat (the key IS the password's) with a P-256 backup key.
      const kdf = { salt: toBase64(randomBytes(16)), iterations: PBKDF2_ITERATIONS };
      const v1Key = await deriveKey(OLD, kdf);
      const v1 = new PrivateVault();
      v1.adopt(v1Key);
      const real = crypto.subtle.generateKey.bind(crypto.subtle);
      const spy = vi.spyOn(crypto.subtle, 'generateKey').mockImplementation(((alg: AlgorithmIdentifier, ...rest: unknown[]) => {
        if ((alg as Algorithm).name === 'X25519') return Promise.reject(new Error('no X25519'));
        return (real as (...args: unknown[]) => Promise<unknown>)(alg, ...rest);
      }) as typeof crypto.subtle.generateKey);
      const p = await makeBackupKey(v1, 1_700_000_000);
      spy.mockRestore();
      const vectors = {
        note: 'Fake chats made by services/ui/src/lib/privateChatBackupKey.test.ts; fake passwords.',
        passwords: { first: FIRST, second: SECOND, v1: OLD },
        v2_chat: {
          token: 'vectorchat2',
          members: ['alexfake', 'samfake'],
          version: 2,
          kdf: change.kdf,
          wrapped_key: change.wrapped_key,
          verifier: change.verifier,
          auth_hash: 'not-a-real-hash',
          history: [{ version: 2, kdf: made.kdf, wrapped_key: made.wrapped_key, verifier: made.verifier, auth_hash: 'old', replaced_by: 'alexfake' }],
          backup_key: { alg: x.alg, public_key: x.public_key, sealed_private_key: x.sealed_private_key, key_id: 'vectorx', created_at: x.created_at },
        },
        v1_chat: {
          token: 'vectorchat1',
          members: ['alexfake', 'samfake'],
          salt: kdf.salt,
          iterations: kdf.iterations,
          verifier: await makeVerifier(v1Key),
          backup_key: { alg: p.alg, public_key: p.public_key, sealed_private_key: p.sealed_private_key, key_id: 'vectorp', created_at: p.created_at },
        },
      };
      writeFileSync(VECTORS, `${JSON.stringify(vectors, null, 2)}\n`);
    }
    const vectors = JSON.parse(readFileSync(VECTORS, 'utf8'));
    // Both passwords open the v2 chat (the current record and its history), and its key opens the sealed private key.
    const v2 = vectors.v2_chat;
    const current = new PrivateVault();
    expect(await current.unlock(SECOND, { version: 2, kdf: v2.kdf, wrapped_key: v2.wrapped_key, verifier: v2.verifier })).toBe(true);
    expect(await openBackupKey(current, v2.backup_key.sealed_private_key)).toMatchObject({ alg: 'X25519', public_key: v2.backup_key.public_key });
    const old = v2.history[0];
    expect(await new PrivateVault().unlock(FIRST, { version: 2, kdf: old.kdf, wrapped_key: old.wrapped_key, verifier: old.verifier })).toBe(true);
    const v1 = vectors.v1_chat;
    const legacy = new PrivateVault();
    expect(await legacy.unlock(OLD, { version: 1, kdf: { salt: v1.salt, iterations: v1.iterations }, verifier: v1.verifier })).toBe(true);
    expect(await openBackupKey(legacy, v1.backup_key.sealed_private_key)).toMatchObject({ alg: 'P-256', public_key: v1.backup_key.public_key });
  });
});
