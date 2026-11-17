import { beforeEach, describe, expect, it } from 'vitest';
import {
  CALL_AUTH_VERSION,
  PeerVerifier,
  checkFingerprints,
  deriveCallKey,
  isKnownPrivateRoom,
  proveFingerprints,
  rememberPrivateRoom,
  resetSeenNonces,
  sdpFingerprints,
} from './callVerify';
import { PrivateVault } from './privateChatCrypto';

/** The private chat's data key, as an unlocked vault holds it: AES-GCM, not extractable. */
async function dataKey(seed: number): Promise<CryptoKey> {
  const bytes = new Uint8Array(32).fill(seed);
  return crypto.subtle.importKey('raw', bytes, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
}

const FP_A = ['sha-256 AA:AA:AA'];
const FP_B = ['sha-256 BB:BB:BB'];
const FP_EVIL = ['sha-256 EE:EE:EE'];

/** Two parents' verifiers for one call, with each one's hello delivered to the other. */
async function pair(room = 'pc-room', keyA?: CryptoKey, keyB?: CryptoKey) {
  const a = new PeerVerifier(keyA ?? await deriveCallKey(await dataKey(7)), room, new Set());
  const b = new PeerVerifier(keyB ?? await deriveCallKey(await dataKey(7)), room, new Set());
  expect(a.receive(b.hello())).toBe('ok');
  expect(b.receive(a.hello())).toBe('ok');
  return { a, b };
}

describe('the fingerprints an SDP names', () => {
  it('reads every fingerprint line, normalised, sorted and once each', () => {
    const sdp = 'v=0\r\na=fingerprint:SHA-256 ab:cd:ef\r\nm=audio 9\r\na=fingerprint:sha-256 AB:CD:EF\r\n'
      + 'm=video 9\r\na=fingerprint:sha-1 01:02\r\n';
    expect(sdpFingerprints(sdp)).toEqual(['sha-1 01:02', 'sha-256 AB:CD:EF']);
    expect(sdpFingerprints(undefined)).toEqual([]);
    expect(sdpFingerprints('v=0\r\n')).toEqual([]);
  });
});

describe('verifying a private chat call', () => {
  beforeEach(() => resetSeenNonces());

  it('two devices with the chat key verify each other', async () => {
    const { a, b } = await pair();
    const proofA = await a.prove(FP_A, FP_B);
    const proofB = await b.prove(FP_B, FP_A);
    expect(proofA?.step).toBe('proof');
    expect(await a.prove(FP_A, FP_B)).toBeNull();  // once
    expect(b.receive(proofA)).toBe('ok');
    expect(a.receive(proofB)).toBe('ok');
    expect(await b.check(FP_B, FP_A)).toBe('verified');
    expect(await a.check(FP_A, FP_B)).toBe('verified');
    expect(a.verifiedRemote).toEqual(FP_B);
  });

  it('the same chat key gives the same call key from any unlocked vault, and the proof carries no key', async () => {
    const vaultA = new PrivateVault();
    const vaultB = new PrivateVault();
    vaultA.adopt(await dataKey(9));
    vaultB.adopt(await dataKey(9));
    const { a, b } = await pair('pc-room', await vaultA.callKey(), await vaultB.callKey());
    const proof = await a.prove(FP_A, FP_B);
    b.receive(proof);
    expect(await b.check(FP_B, FP_A)).toBe('verified');
    // Only a nonce and a 32-byte MAC travel: nothing that could stand in for a key.
    expect(Object.keys(proof!).sort()).toEqual(['mac', 'step', 'v']);
    expect(Object.keys(a.hello()).sort()).toEqual(['nonce', 'step', 'v']);
  });

  it('a gateway that swaps a fingerprint is caught by both sides', async () => {
    // The gateway gave Robin its own certificate in place of Casey's, and Casey its own in place of Robin's.
    const { a, b } = await pair();
    b.receive(await a.prove(FP_A, FP_EVIL));  // Robin saw the evil fingerprint as Casey's
    a.receive(await b.prove(FP_B, FP_EVIL));  // Casey saw it as Robin's
    expect(await b.check(FP_B, FP_EVIL)).toBe('failed');
    expect(await a.check(FP_A, FP_EVIL)).toBe('failed');
  });

  it('a swap in one direction only still fails', async () => {
    const { a, b } = await pair();
    b.receive(await a.prove(FP_A, FP_B));
    expect(await b.check(FP_B, FP_EVIL)).toBe('failed');
  });

  it('without the chat key no proof passes', async () => {
    const { a, b } = await pair('pc-room', await deriveCallKey(await dataKey(1)), await deriveCallKey(await dataKey(2)));
    b.receive(await a.prove(FP_A, FP_B));
    expect(await b.check(FP_B, FP_A)).toBe('failed');
  });

  it('a missing proof never verifies, and a missing hello means no proof is made', async () => {
    const { b } = await pair();
    expect(await b.check(FP_B, FP_A)).toBe('pending');
    expect(b.verified).toBe(false);
    const lonely = new PeerVerifier(await deriveCallKey(await dataKey(7)), 'pc-room', new Set());
    expect(await lonely.prove(FP_A, FP_B)).toBeNull();
    expect(await lonely.check(FP_A, FP_B)).toBe('pending');
  });

  it('a nonce seen before is a replay', async () => {
    const seen = new Set<string>();
    const key = await deriveCallKey(await dataKey(7));
    const first = new PeerVerifier(key, 'pc-room', seen);
    const old = new PeerVerifier(key, 'pc-room', seen);
    expect(first.receive(old.hello())).toBe('ok');
    const second = new PeerVerifier(key, 'pc-room', seen);
    expect(second.receive(old.hello())).toBe('replay');
  });

  it('a proof recorded in an earlier call fails in a new one', async () => {
    const key = await deriveCallKey(await dataKey(7));
    const earlier = await pair('pc-room', key, key);
    const recorded = await earlier.a.prove(FP_A, FP_B);
    const now = await pair('pc-room', key, key);
    now.b.receive(recorded);
    expect(await now.b.check(FP_B, FP_A)).toBe('failed');
  });

  it('a proof reflected back at its sender fails', async () => {
    const { a } = await pair();
    const own = await a.prove(FP_A, FP_B);
    a.receive(own);
    expect(await a.check(FP_A, FP_B)).toBe('failed');
  });

  it('a proof for another room fails', async () => {
    const key = await deriveCallKey(await dataKey(7));
    const a = new PeerVerifier(key, 'room-one', new Set());
    const b = new PeerVerifier(key, 'room-two', new Set());
    a.receive(b.hello());
    b.receive(a.hello());
    b.receive(await a.prove(FP_A, FP_B));
    expect(await b.check(FP_B, FP_A)).toBe('failed');
  });

  it('a later description naming another certificate is refused', async () => {
    const { a, b } = await pair();
    a.receive(await b.prove(FP_B, FP_A));
    expect(await a.check(FP_A, FP_B)).toBe('verified');
    expect(await a.check(FP_A, FP_B)).toBe('verified');
    expect(await a.check(FP_A, FP_EVIL)).toBe('failed');
  });

  it('malformed messages are refused', async () => {
    const v = new PeerVerifier(await deriveCallKey(await dataKey(7)), 'pc-room', new Set());
    expect(v.receive(null)).toBe('invalid');
    expect(v.receive({ v: 2, step: 'hello', nonce: 'AAAAAAAAAAAAAAAAAAAAAA' })).toBe('invalid');
    expect(v.receive({ v: CALL_AUTH_VERSION, step: 'hello', nonce: 'short' })).toBe('invalid');
    expect(v.receive({ v: CALL_AUTH_VERSION, step: 'hello', nonce: v.nonce })).toBe('invalid');
    expect(v.receive({ v: CALL_AUTH_VERSION, step: 'proof', mac: 5 })).toBe('invalid');
    expect(v.receive({ v: CALL_AUTH_VERSION, step: 'other' })).toBe('invalid');
    expect(v.receive({ v: CALL_AUTH_VERSION, step: 'hello', nonce: 'AAAAAAAAAAAAAAAAAAAAAA' })).toBe('ok');
    // A second, different hello from the same peer is not accepted.
    expect(v.receive({ v: CALL_AUTH_VERSION, step: 'hello', nonce: 'BBBBBBBBBBBBBBBBBBBBBB' })).toBe('invalid');
  });

  it('a MAC of the wrong length or alphabet is simply false', async () => {
    const key = await deriveCallKey(await dataKey(7));
    const fields = { room: 'r', senderNonce: 'a', receiverNonce: 'b', senderFingerprints: FP_A, receiverFingerprints: FP_B };
    const mac = await proveFingerprints(key, fields);
    expect(await checkFingerprints(key, fields, mac)).toBe(true);
    expect(await checkFingerprints(key, fields, mac.slice(0, 20))).toBe(false);
    expect(await checkFingerprints(key, fields, '!!!')).toBe(false);
  });
});

describe('rooms known to be private', () => {
  it('are remembered on this device', () => {
    localStorage.removeItem('jarvis.calls.privateRooms');
    expect(isKnownPrivateRoom('pc-room')).toBe(false);
    rememberPrivateRoom('pc-room');
    expect(isKnownPrivateRoom('pc-room')).toBe(true);
    expect(isKnownPrivateRoom('family')).toBe(false);
    localStorage.removeItem('jarvis.calls.privateRooms');
  });
});
