/** Types for privateChatBackupCore.js (plain JavaScript so the encrypted keepsake page can embed it verbatim). */

export const BACKUP_SUFFIX: string;
export const CHUNK_BYTES: number;
export const IMAGE_MIMES: string[];
export const VIDEO_MIMES: string[];
export const BELOVED_SECTIONS: Array<[BelovedKind, string]>;

export type BelovedKind = 'profile' | 'day' | 'note' | 'heart' | 'prayer' | 'devotion' | 'idea' | 'together' | 'plan';

export class BackupError extends Error {
  code: 'format' | 'password' | 'tampered';
  constructor(message: string, code?: 'format' | 'password' | 'tampered');
}

/** A key record as the server keeps it (v1 or v2): public parameters only. */
export interface BackupKeyRecord {
  version?: 1 | 2;
  kdf: { salt: string; iterations: number };
  wrapped_key?: { iv: string; ct: string };
  verifier: { iv: string; ct: string };
}

export interface BackupHeader {
  format: string;
  version: number;
  payload: string;
  token: string;
  created_at: number;
  alg: 'X25519' | 'P-256';
  kdf: string;
  cipher: string;
  chunk_bytes: number;
  key_id: string;
  recipient_public_key: string;
  ephemeral_public_key: string;
  hkdf_salt: string;
  sealed_private_key: { iv: string; ct: string };
  key_records: BackupKeyRecord[];
}

export interface OpenedBackup {
  header: BackupHeader;
  token: string;
  /** The chat's data key (never extractable): it opens the sealed records and pictures inside. */
  key: CryptoKey;
  /** Every file in the chat's folder, by its path in the backup (still sealed where the chat seals it). */
  files: Map<string, Blob>;
}

export interface KeepsakeMessage {
  id: number;
  by: string;
  at: number;
  replyTo: number | null;
  deleted: boolean;
  /** What the message says once opened (text, image, video, comment); null when deleted or unreadable. */
  payload: Record<string, unknown> | null;
  unreadable: boolean;
  edit: { text: string; at: number } | null;
  reactions: Array<{ by: string; emoji: string; at: number }>;
}

export interface KeepsakePicture {
  id: string;
  by: string;
  at: number;
  payload: Record<string, unknown> & { blob: string; mime?: string };
}

export interface KeepsakeBeloved {
  kind: BelovedKind;
  id: string;
  by: string;
  at: number;
  updatedAt: number;
  record: Record<string, unknown>;
}

export interface Keepsake {
  token: string;
  /** When the backup was made (seconds). */
  madeAt: number;
  createdAt: number;
  members: string[];
  messages: KeepsakeMessage[];
  gallery: KeepsakePicture[];
  beloved: KeepsakeBeloved[];
  blobs: Map<string, Blob>;
  read: Record<string, number>;
  key: CryptoKey;
}

export function readBackupHeader(source: Blob): Promise<{ header: BackupHeader; digest: Uint8Array; offset: number }>;
export function openBackup(
  source: Blob,
  password: string,
  options?: { moreRecords?: BackupKeyRecord[]; onProgress?: (done: number, total: number) => void },
): Promise<OpenedBackup>;
export function readKeepsake(opened: OpenedBackup): Promise<Keepsake>;
export function openSealed(key: CryptoKey, raw: unknown): Promise<unknown>;
export function decryptBlob(key: CryptoKey, blob: Blob): Promise<Uint8Array<ArrayBuffer>>;
export function decryptClip(keepsake: Keepsake, video: Record<string, unknown>): Promise<Blob>;
export function describeBeloved(kind: BelovedKind, record: Record<string, unknown>): { title: string; lines: string[] };
export function pictureType(mime: unknown): string;
export function belongsTo(path: string, token: string): boolean;
