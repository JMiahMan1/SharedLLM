/**
 * The private chat's backup key: what lets the server back the chat up so
 * that only the chat password can open the backup.
 *
 * The first time a chat is unlocked on any screen (the thread, Beloved, the
 * gallery, a call: every vault comes from `vaultFor`) and the server says it
 * has no backup key yet, this device makes an ECDH key pair, seals the private
 * key with the chat's data key (`sealRecord`, the same envelope as a message,
 * record kind `backup-key`) and uploads the public key with the sealed private
 * key. The nightly backup (scripts/private_chat_backup.py) encrypts the
 * chat's data to the public key; opening it needs the private key, which only
 * the chat's data key unseals, which only the password unwraps
 * (scripts/open_private_chat_backup.py).
 *
 * X25519 where the WebView has it (Chrome/Android WebView 133+, Safari 17+);
 * ECDH P-256 otherwise. The server never replaces a key pair unless asked to
 * rotate, and a password change does not touch it (the data key is unchanged).
 */
import { toBase64, type PrivateVault } from './privateChatCrypto';

export const BACKUP_KEY_RECORD = 'backup-key';
export type BackupAlg = 'X25519' | 'P-256';

/** What the chat's record says about its backup key (no secrets). */
export interface BackupKeyInfo {
  alg: BackupAlg;
  public_key: string;
  key_id: string;
  created_at: number;
}

/** The upload: the public key and the private key sealed with the chat's key. */
export interface BackupKeyUpload {
  alg: BackupAlg;
  public_key: string;
  sealed_private_key: string;
  created_at: number;
  rotate?: boolean;
}

/** What is sealed: the PKCS#8 private key and the public key it belongs to. */
export interface BackupKeyRecord {
  t: typeof BACKUP_KEY_RECORD;
  v: 1;
  alg: BackupAlg;
  private_key: string;
  public_key: string;
  created_at: number;
}

function subtle(): SubtleCrypto {
  const s = globalThis.crypto?.subtle;
  if (!s) throw new Error('WebCrypto is not available.');
  return s;
}

/** A fresh key pair: X25519, or P-256 where the WebView cannot do X25519. */
export async function generateBackupKeyPair(): Promise<{ alg: BackupAlg; keys: CryptoKeyPair }> {
  try {
    const keys = (await subtle().generateKey({ name: 'X25519' }, true, ['deriveBits'])) as CryptoKeyPair;
    return { alg: 'X25519', keys };
  } catch {
    const keys = (await subtle().generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits'])) as CryptoKeyPair;
    return { alg: 'P-256', keys };
  }
}

/** A new backup key pair for an unlocked chat, ready to upload. The private key's bytes are zeroed once sealed. */
export async function makeBackupKey(vault: PrivateVault, now: number = Math.floor(Date.now() / 1000)): Promise<BackupKeyUpload> {
  const { alg, keys } = await generateBackupKeyPair();
  const publicKey = toBase64(new Uint8Array(await subtle().exportKey('raw', keys.publicKey)));
  const pkcs8 = new Uint8Array(await subtle().exportKey('pkcs8', keys.privateKey));
  try {
    const record: BackupKeyRecord = { t: BACKUP_KEY_RECORD, v: 1, alg, private_key: toBase64(pkcs8), public_key: publicKey, created_at: now };
    return { alg, public_key: publicKey, sealed_private_key: await vault.sealRecord(record), created_at: now };
  } finally {
    pkcs8.fill(0);
  }
}

/** A sealed backup key opened with the chat's key, or null when it is not one. */
export async function openBackupKey(vault: PrivateVault, sealed: string): Promise<BackupKeyRecord | null> {
  const record = (await vault.openRecord(sealed)) as Partial<BackupKeyRecord> | null;
  if (!record || record.t !== BACKUP_KEY_RECORD || (record.alg !== 'X25519' && record.alg !== 'P-256')) return null;
  if (typeof record.private_key !== 'string' || typeof record.public_key !== 'string') return null;
  return record as BackupKeyRecord;
}

/** How this module reaches the server; set by services/api.ts so this file has no network code of its own. */
export interface BackupKeyClient {
  getChat(token: string): Promise<{ backup_key?: BackupKeyInfo | null }>;
  putBackupKey(token: string, body: BackupKeyUpload): Promise<unknown>;
}

let client: BackupKeyClient | null = null;
const settled = new Set<string>();
const inFlight = new Map<string, Promise<EnsureResult>>();

export function configureBackupKeys(next: BackupKeyClient | null): void {
  client = next;
}

export type EnsureResult = 'exists' | 'made' | 'raced' | 'skipped';

function status(error: unknown): number | undefined {
  return (error as { response?: { status?: number } })?.response?.status;
}

/**
 * Make and upload the chat's backup key if it has none. Once per chat per app
 * run; a 409 (the other parent's phone was quicker) is success.
 */
export function ensureBackupKey(token: string, vault: PrivateVault): Promise<EnsureResult> {
  if (!client || settled.has(token) || !vault.unlocked) return Promise.resolve('skipped');
  const running = inFlight.get(token);
  if (running) return running;
  const api = client;
  const job = (async (): Promise<EnsureResult> => {
    const chat = await api.getChat(token);
    if (chat.backup_key) return 'exists';
    if (!vault.unlocked) return 'skipped';
    try {
      await api.putBackupKey(token, await makeBackupKey(vault));
      return 'made';
    } catch (error) {
      if (status(error) === 409) return 'raced';
      throw error;
    }
  })()
    .then((result) => {
      if (result !== 'skipped') settled.add(token);
      return result;
    })
    .finally(() => inFlight.delete(token));
  inFlight.set(token, job);
  return job;
}

/** Watch a chat's vault: whenever it is unlocked, make sure the chat has a backup key. Errors are retried on the next unlock. */
export function watchVaultForBackupKey(token: string, vault: PrivateVault): () => void {
  const check = () => {
    if (vault.unlocked) void ensureBackupKey(token, vault).catch(() => {});
  };
  check();
  return vault.subscribe(check);
}

/** For tests: forget which chats were already checked. */
export function resetBackupKeys(): void {
  settled.clear();
  inFlight.clear();
}
