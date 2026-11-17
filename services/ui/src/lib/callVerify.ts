/**
 * End-to-end verification of a call in the parents' private chat.
 *
 * Call media is DTLS-SRTP between the two devices, and each side's DTLS
 * certificate is named by the `a=fingerprint` lines in its SDP. The gateway
 * relays that SDP, so a compromised gateway could swap the fingerprints and
 * sit in the middle of the call. In the private chat both devices hold the
 * chat's data key (DK), which the server never has, so each one proves its
 * fingerprints with it:
 *
 * 1. **Key.** `deriveCallKey(DK)` makes an HMAC-SHA256 key on the device. DK is
 *    a non-extractable AES-GCM key, so its bytes are never available: the
 *    device encrypts one fixed block under DK with one fixed IV (a
 *    deterministic PRF output only a holder of DK can compute; the IV is
 *    reserved for this and is never a message's random IV) and feeds that
 *    through HKDF-SHA256 with the label `jarvis-call-fingerprint-v1`. Neither
 *    DK nor the derived key, nor the PRF block, ever leaves the device.
 * 2. **Hello.** Each side sends the other a fresh random nonce (16 bytes) for
 *    this call, through the gateway, as an opaque `auth` message.
 * 3. **Proof.** Once a side has both descriptions, it sends
 *    HMAC(key, label | room | its nonce | the other's nonce | its own
 *    fingerprints | the fingerprints it received). The other side recomputes
 *    the same MAC from its own view (the fingerprints it received from the
 *    sender, and its own) and accepts the peer only on an exact match.
 *
 * A swapped fingerprint in either direction makes the two views differ, so the
 * MAC fails. Both nonces are in every MAC, so a proof recorded from an earlier
 * call cannot be replayed into a new one, and a nonce seen before is refused
 * outright. A peer that sends no proof is never accepted. Until a peer is
 * verified no media is sent to it and nothing it sends is played.
 */

export const CALL_AUTH_LABEL = 'jarvis-call-fingerprint-v1';
export const CALL_AUTH_VERSION = 1;
export const NONCE_BYTES = 16;
/** How long a peer has to prove itself before the call is refused as unverified. */
export const VERIFY_TIMEOUT_MS = 20_000;
export const UNVERIFIED_MESSAGE =
  'This call could not be verified, so it was ended. It may not have been private. Try calling again.';

const encoder = new TextEncoder();

function subtle(): SubtleCrypto {
  const s = globalThis.crypto?.subtle;
  if (!s) throw new Error('This device cannot verify a private call.');
  return s;
}

function toBase64Url(bytes: Uint8Array): string {
  let text = '';
  for (const b of bytes) text += String.fromCharCode(b);
  return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function fromBase64Url(text: string): Uint8Array<ArrayBuffer> | null {
  if (!/^[A-Za-z0-9_-]+$/.test(text)) return null;
  try {
    const raw = atob(text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (text.length % 4)) % 4));
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  } catch {
    return null;
  }
}

/** A fresh nonce for one side of one call. */
export function newCallNonce(): string {
  return toBase64Url(globalThis.crypto.getRandomValues(new Uint8Array(NONCE_BYTES)));
}

const NONCE_RE = new RegExp(`^[A-Za-z0-9_-]{${Math.ceil((NONCE_BYTES * 4) / 3)}}$`);

/**
 * The DTLS fingerprints an SDP names, normalised (`sha-256 AB:CD:...`), sorted
 * and without repeats, so the same certificate always reads the same.
 */
export function sdpFingerprints(sdp: string | null | undefined): string[] {
  if (!sdp) return [];
  const found = new Set<string>();
  for (const match of sdp.matchAll(/^a=fingerprint:(\S+)\s+([0-9A-Fa-f:]+)\s*$/gm)) {
    found.add(`${match[1].toLowerCase()} ${match[2].toUpperCase()}`);
  }
  return [...found].sort();
}

/** The call key from the chat's data key: HMAC-SHA256, never extractable (see the module comment). */
export async function deriveCallKey(dataKey: CryptoKey): Promise<CryptoKey> {
  const s = subtle();
  const label = encoder.encode(CALL_AUTH_LABEL);
  // The reserved IV: the first 12 bytes of SHA-256 of the label, never a message's random IV.
  const iv = new Uint8Array(await s.digest('SHA-256', label)).slice(0, 12);
  const block = new Uint8Array(await s.encrypt({ name: 'AES-GCM', iv }, dataKey, encoder.encode(`${CALL_AUTH_LABEL} key`)));
  try {
    const base = await s.importKey('raw', block, 'HKDF', false, ['deriveKey']);
    return await s.deriveKey(
      { name: 'HKDF', hash: 'SHA-256', salt: new Uint8Array(32), info: label },
      base,
      { name: 'HMAC', hash: 'SHA-256', length: 256 },
      false,
      ['sign', 'verify'],
    );
  } finally {
    block.fill(0);
  }
}

export interface ProofFields {
  room: string;
  senderNonce: string;
  receiverNonce: string;
  senderFingerprints: string[];
  receiverFingerprints: string[];
}

/** Exactly what a proof covers, as bytes. JSON keeps the fields unambiguous. */
export function proofMessage(f: ProofFields): Uint8Array<ArrayBuffer> {
  return encoder.encode(JSON.stringify([
    CALL_AUTH_LABEL, CALL_AUTH_VERSION, f.room, f.senderNonce, f.receiverNonce,
    [...f.senderFingerprints].sort(), [...f.receiverFingerprints].sort(),
  ]));
}

export async function proveFingerprints(key: CryptoKey, f: ProofFields): Promise<string> {
  return toBase64Url(new Uint8Array(await subtle().sign('HMAC', key, proofMessage(f))));
}

/** Constant-time (WebCrypto's verify) check of a proof against this side's own view. */
export async function checkFingerprints(key: CryptoKey, f: ProofFields, mac: string): Promise<boolean> {
  const bytes = typeof mac === 'string' ? fromBase64Url(mac) : null;
  if (!bytes || bytes.length !== 32) return false;
  return subtle().verify('HMAC', key, bytes, proofMessage(f));
}

export type AuthMessage =
  | { v: typeof CALL_AUTH_VERSION; step: 'hello'; nonce: string }
  | { v: typeof CALL_AUTH_VERSION; step: 'proof'; mac: string };

/** Nonces this app has seen from peers since it started: one seen again is a replay. */
const seenPeerNonces = new Set<string>();

export type ReceiveResult = 'ok' | 'replay' | 'invalid';
export type CheckResult = 'verified' | 'pending' | 'failed';

/**
 * One side's verification of one peer. The call session feeds it the peer's
 * `auth` messages and its current local and remote SDP; it says when to send
 * a proof and whether the peer is verified.
 */
export class PeerVerifier {
  readonly nonce = newCallNonce();
  private peerNonce: string | null = null;
  private peerMac: string | null = null;
  private proved = false;
  private checking = false;
  verified = false;
  failed = false;
  /** The peer's fingerprints as verified: a later SDP naming others is refused. */
  verifiedRemote: string[] = [];

  private readonly key: CryptoKey;
  private readonly room: string;
  private readonly seen: Set<string>;

  constructor(key: CryptoKey, room: string, seen: Set<string> = seenPeerNonces) {
    this.key = key;
    this.room = room;
    this.seen = seen;
  }

  hello(): AuthMessage {
    return { v: CALL_AUTH_VERSION, step: 'hello', nonce: this.nonce };
  }

  /** Take one `auth` message from the peer. A reused nonce is a replay; anything malformed is invalid. */
  receive(message: unknown): ReceiveResult {
    if (!message || typeof message !== 'object') return 'invalid';
    const m = message as Record<string, unknown>;
    if (m.v !== CALL_AUTH_VERSION) return 'invalid';
    if (m.step === 'hello') {
      const nonce = m.nonce;
      if (typeof nonce !== 'string' || !NONCE_RE.test(nonce) || nonce === this.nonce) return 'invalid';
      if (this.peerNonce !== null) return nonce === this.peerNonce ? 'ok' : 'invalid';
      if (this.seen.has(nonce)) return 'replay';
      this.seen.add(nonce);
      this.peerNonce = nonce;
      return 'ok';
    }
    if (m.step === 'proof') {
      if (typeof m.mac !== 'string' || m.mac.length > 128) return 'invalid';
      if (this.peerMac !== null && this.peerMac !== m.mac) return 'invalid';
      this.peerMac = m.mac;
      return 'ok';
    }
    return 'invalid';
  }

  /** This side's proof, once: when the peer's nonce and both sides' fingerprints are known. */
  async prove(local: string[], remote: string[]): Promise<AuthMessage | null> {
    if (this.proved || this.failed || !this.peerNonce || !local.length || !remote.length) return null;
    this.proved = true;
    const mac = await proveFingerprints(this.key, {
      room: this.room,
      senderNonce: this.nonce,
      receiverNonce: this.peerNonce,
      senderFingerprints: local,
      receiverFingerprints: remote,
    });
    return { v: CALL_AUTH_VERSION, step: 'proof', mac };
  }

  /** Check the peer's proof against this side's own view of both fingerprints. */
  async check(local: string[], remote: string[]): Promise<CheckResult> {
    if (this.failed) return 'failed';
    if (this.verified) return this.sameRemote(remote) ? 'verified' : this.fail();
    if (this.checking || !this.peerNonce || !this.peerMac || !local.length || !remote.length) return 'pending';
    this.checking = true;
    try {
      const ok = await checkFingerprints(this.key, {
        room: this.room,
        senderNonce: this.peerNonce,
        receiverNonce: this.nonce,
        senderFingerprints: remote,
        receiverFingerprints: local,
      }, this.peerMac);
      if (!ok) return this.fail();
      this.verified = true;
      this.verifiedRemote = [...remote].sort();
      return 'verified';
    } finally {
      this.checking = false;
    }
  }

  /** Whether a remote SDP still names the verified certificate (empty means none given yet). */
  sameRemote(remote: string[]): boolean {
    if (!remote.length) return true;
    const sorted = [...remote].sort();
    return sorted.length === this.verifiedRemote.length && sorted.every((f, i) => f === this.verifiedRemote[i]);
  }

  private fail(): CheckResult {
    this.failed = true;
    this.verified = false;
    return 'failed';
  }
}

const KNOWN_PRIVATE_KEY = 'jarvis.calls.privateRooms';

function knownPrivateRooms(): string[] {
  try {
    const raw = globalThis.localStorage?.getItem(KNOWN_PRIVATE_KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list.filter((t): t is string => typeof t === 'string') : [];
  } catch {
    return [];
  }
}

/**
 * Whether this device has seen ``room`` as a private chat. Remembered here so a gateway that later stops
 * saying so cannot talk this device into an unverified call in that room.
 */
export function isKnownPrivateRoom(room: string): boolean {
  return knownPrivateRooms().includes(room);
}

export function rememberPrivateRoom(room: string): void {
  if (!room || isKnownPrivateRoom(room)) return;
  try {
    globalThis.localStorage?.setItem(KNOWN_PRIVATE_KEY, JSON.stringify([...knownPrivateRooms(), room].slice(-20)));
  } catch {
    // Storage full or blocked: the chat's own flag and the gateway's still apply.
  }
}

/** For tests: forget the nonces seen so far. */
export function resetSeenNonces(): void {
  seenPeerNonces.clear();
}
