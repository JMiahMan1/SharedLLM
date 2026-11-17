import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useEffect } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider, useAuth } from '../context/AuthContext';
import { CallProvider, CallSession, type CallPeer, type CallProblem, useCall } from '../context/CallContext';
import { UNVERIFIED_MESSAGE, deriveCallKey, resetSeenNonces } from '../lib/callVerify';
import { lockNow, vaultFor } from '../lib/privateChatSession';

/**
 * A private chat call between two real call sessions, over an in-memory stand-in for the gateway that can
 * misbehave the way a compromised one would: swap the DTLS fingerprints in the SDP it relays, or drop the
 * proofs. Peer connections are fakes that name one certificate each. Made-up parents, Robin and Casey.
 */

vi.mock('../lib/dropIn', () => ({
  listenForDropIn: () => () => {},
  reportDropInEnded: vi.fn(),
  reportDropInJoined: vi.fn(),
  nativeDropInStatus: () => Promise.resolve(null),
}));

const auth = vi.hoisted(() => ({ signedIn: false }));
vi.mock('../services/api', async (original) => {
  const real = await original<typeof import('../services/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      ringCall: () => Promise.resolve({ ringing: [] }),
      getCallList: () => Promise.resolve({ calls: [], deviceDropIn: false, deviceDropInVideo: false }),
      getFamilyDirectory: () => Promise.resolve([]),
      getMe: () => { auth.signedIn = true; return Promise.resolve({ username: 'robin', is_admin: false }); },
    },
  };
});

type Msg = Record<string, unknown>;

/** The gateway's relay, in memory. ``tamper`` may rewrite or drop a relayed message. */
class Relay {
  sockets = new Map<string, RelaySocket>();
  next = 0;
  tamper: (message: Msg) => Msg | null = (m) => m;
  join(socket: RelaySocket): void {
    const id = `peer-${++this.next}`;
    socket.id = id;
    const others = [...this.sockets.values()];
    this.sockets.set(id, socket);
    const me = { id, username: socket.user, name: socket.user, muted: false, camera: false };
    socket.deliver({ type: 'welcome', self: me, peers: others.map((o) => ({ id: o.id, username: o.user, name: o.user })),
      ice_servers: [], room: 'pc-room', private: true });
    for (const o of others) o.deliver({ type: 'peer-joined', from: id, peer: me });
  }
  relay(from: RelaySocket, message: Msg): void {
    const target = this.sockets.get(String(message.to));
    if (!target) return;
    const out = this.tamper({ ...message, from: from.id });
    if (out) target.deliver(out);
  }
}

let relay = new Relay();

class RelaySocket {
  static OPEN = 1;
  readyState = 0;
  id = '';
  user: string;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(url: string) {
    this.user = new URL(url).searchParams.get('token') || '';
    setTimeout(() => {
      this.readyState = 1;
      relay.join(this);
    }, 0);
  }
  deliver(message: Msg) {
    setTimeout(() => this.onmessage?.({ data: JSON.stringify(message) }), 0);
  }
  send(data: string) {
    const message = JSON.parse(data) as Msg;
    if (['offer', 'answer', 'ice', 'auth'].includes(String(message.type))) relay.relay(this, message);
  }
  close() { this.readyState = 3; }
}

class FakeSender {
  track: unknown;

  constructor(track: unknown) { this.track = track; }
  replaceTrack(track: unknown) { this.track = track; return Promise.resolve(); }
}

let certificates = 0;
class FakePeerConnection {
  readonly fingerprint = `sha-256 ${String(++certificates).padStart(2, '0')}:AB:CD`;
  signalingState = 'stable';
  connectionState = 'new';
  localDescription: { type: string; sdp: string } | null = null;
  remoteDescription: { type: string; sdp: string } | null = null;
  senders: FakeSender[] = [];
  onicecandidate = null;
  ontrack = null;
  onnegotiationneeded: (() => void) | null = null;
  onconnectionstatechange = null;
  private pending = false;
  private needed() {
    if (this.pending) return;
    this.pending = true;
    queueMicrotask(() => { this.pending = false; this.onnegotiationneeded?.(); });
  }
  addTrack(track: unknown) { const s = new FakeSender(track); this.senders.push(s); this.needed(); return s; }
  addTransceiver() { const s = new FakeSender(null); this.senders.push(s); this.needed(); return { sender: s }; }
  getSenders() { return this.senders; }
  removeTrack(sender: FakeSender) { sender.track = null; }
  setLocalDescription(description?: { type: string }) {
    if (description?.type === 'rollback') {
      this.signalingState = 'stable';
      this.localDescription = null;
      return Promise.resolve();
    }
    const type = this.signalingState === 'have-remote-offer' ? 'answer' : 'offer';
    this.localDescription = { type, sdp: `v=0\r\na=fingerprint:${this.fingerprint}\r\n` };
    this.signalingState = type === 'offer' ? 'have-local-offer' : 'stable';
    return Promise.resolve();
  }
  setRemoteDescription(description: { type: string; sdp: string }) {
    this.remoteDescription = description;
    this.signalingState = description.type === 'offer' ? 'have-remote-offer' : 'stable';
    return Promise.resolve();
  }
  addIceCandidate() { return Promise.resolve(); }
  close() {}
}

class FakeStream {
  tracks: unknown[];
  constructor(tracks: unknown[] = []) { this.tracks = [...tracks]; }
  addTrack(t: unknown) { this.tracks.push(t); }
  removeTrack(t: unknown) { this.tracks = this.tracks.filter((x) => x !== t); }
  getTracks() { return [...this.tracks]; }
  getAudioTracks() { return this.tracks; }
  getVideoTracks() { return []; }
}

async function chatKey(seed = 5): Promise<CryptoKey> {
  return crypto.subtle.importKey('raw', new Uint8Array(32).fill(seed), { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
}

interface Side { session: CallSession; peers: CallPeer[]; ended: { message: string; problem?: CallProblem } | null; mic: unknown }

async function side(user: string, seed = 5): Promise<Side> {
  const out: Side = { session: null as unknown as CallSession, peers: [], ended: null, mic: { kind: 'audio', enabled: true, stop() {} } };
  const mic = out.mic;
  const devices = navigator.mediaDevices as unknown as Record<string, unknown>;
  devices.getUserMedia = () => Promise.resolve(new FakeStream([mic]));
  out.session = new CallSession(user, 'pc-room', 'Private', {
    onPeers: (peers) => { out.peers = peers; },
    onLocalStream: () => {},
    onEnded: (message, problem) => { out.ended = { message, problem }; },
  }, { private: true, callKey: await deriveCallKey(await chatKey(seed)) });
  await out.session.start();
  return out;
}

const settle = () => act(async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 5)); });

/** Every sender of every peer connection a side made, by the track it carries now. */
function carried(): unknown[] {
  return pcs.flatMap((pc) => pc.senders.map((s) => s.track));
}

let pcs: FakePeerConnection[] = [];

describe('a private chat call, end to end', () => {
  beforeEach(() => {
    relay = new Relay();
    pcs = [];
    resetSeenNonces();
    vi.stubGlobal('WebSocket', RelaySocket);
    vi.stubGlobal('RTCPeerConnection', class extends FakePeerConnection {
      constructor() { super(); pcs.push(this); }
    });
    vi.stubGlobal('MediaStream', FakeStream);
    if (!navigator.mediaDevices) Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {} });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('both sides verify each other, and only then does audio flow and play', async () => {
    const robin = await side('robin');
    const casey = await side('casey');
    await settle();
    expect(robin.ended).toBeNull();
    expect(casey.ended).toBeNull();
    expect(robin.peers.map((p) => p.verified)).toEqual([true]);
    expect(casey.peers.map((p) => p.verified)).toEqual([true]);
    // Each microphone now rides its sender.
    expect(carried()).toEqual(expect.arrayContaining([robin.mic, casey.mic]));
  });

  it('a gateway that swaps the fingerprints gets the call ended as unverified, with no audio sent', async () => {
    relay.tamper = (m) => {
      const sdp = m.sdp as { type: string; sdp: string } | undefined;
      if (!sdp) return m;
      return { ...m, sdp: { ...sdp, sdp: sdp.sdp.replace(/a=fingerprint:.*\r\n/, 'a=fingerprint:sha-256 EE:EE:EE\r\n') } };
    };
    const robin = await side('robin');
    const casey = await side('casey');
    await settle();
    expect(robin.ended).toEqual({ message: UNVERIFIED_MESSAGE, problem: 'unverified' });
    expect(casey.ended).toEqual({ message: UNVERIFIED_MESSAGE, problem: 'unverified' });
    expect(carried().filter(Boolean)).toEqual([]);
  });

  it('a device without the same chat key cannot pass as the other parent', async () => {
    const robin = await side('robin', 5);
    const intruder = await side('casey', 6);
    await settle();
    expect(robin.ended?.problem).toBe('unverified');
    expect(intruder.ended?.problem).toBe('unverified');
  });

  it('a gateway that drops the proofs gets the call ended once the wait runs out', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    relay.tamper = (m) => (m.type === 'auth' ? null : m);
    const robin = await side('robin');
    const casey = await side('casey');
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(robin.ended).toBeNull();
    expect(robin.peers[0]?.stream ?? null).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(21_000); });
    expect(robin.ended?.problem).toBe('unverified');
    expect(casey.ended?.problem).toBe('unverified');
    expect(carried().filter(Boolean)).toEqual([]);
  });

  it('a replayed proof from an earlier call is refused', async () => {
    let recorded: Msg | null = null;
    relay.tamper = (m) => { if (m.type === 'auth' && (m.auth as Msg).step === 'proof') recorded = m; return m; };
    const first = await side('robin');
    const second = await side('casey');
    await settle();
    expect(first.peers[0]?.verified).toBe(true);
    first.session.close();
    second.session.close();
    // A new call: the gateway hands the new device an old proof instead of the real one.
    relay = new Relay();
    relay.tamper = (m) => (m.type === 'auth' && (m.auth as Msg).step === 'proof' && recorded
      ? { ...recorded, from: m.from, to: m.to } : m);
    const robin = await side('robin');
    const casey = await side('casey');
    await settle();
    expect(robin.ended?.problem).toBe('unverified');
    expect(casey.ended?.problem).toBe('unverified');
  });
});

/** The app's side: a private chat call needs the chat unlocked, and says so. */
const grabbed: { calls: ReturnType<typeof useCall> | null; token: string | null } = { calls: null, token: null };
function Grab() {
  const call = useCall();
  const { token } = useAuth();
  useEffect(() => {
    grabbed.calls = call;
    grabbed.token = token;
  });
  return null;
}

describe('calling in a locked private chat', () => {
  beforeEach(() => {
    grabbed.calls = null;
    grabbed.token = null;
    localStorage.setItem('jarvis_api_key', 'test-key');
    localStorage.setItem('jarvis_user', JSON.stringify({ username: 'robin' }));
    vi.stubGlobal('WebSocket', RelaySocket);
    relay = new Relay();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
    lockNow('pc-locked');
  });

  async function renderCalls() {
    auth.signedIn = false;
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AuthProvider>
            <CallProvider><Grab /></CallProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(auth.signedIn).toBe(true));
    await waitFor(() => expect(grabbed.token).toBe('test-key'));
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  }

  it('asks to unlock first and never opens the call', async () => {
    await renderCalls();
    await waitFor(() => expect(grabbed.calls).not.toBeNull());
    const socket = vi.fn();
    vi.stubGlobal('WebSocket', socket);
    await act(async () => { await grabbed.calls!.startCall('pc-locked', 'Private', { private: true }); });
    expect(screen.getByTestId('call-locked')).toBeTruthy();
    expect(screen.getByText('Unlock to call')).toBeTruthy();
    expect(screen.getByTestId('call-open-private-chat')).toBeTruthy();
    expect(socket).not.toHaveBeenCalled();
    expect(grabbed.calls!.call?.problem).toBe('locked');
  });

  it('with the chat unlocked the call starts as a private one', async () => {
    vaultFor('pc-locked').adopt(await chatKey());
    if (!navigator.mediaDevices) Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {} });
    (navigator.mediaDevices as unknown as Record<string, unknown>).getUserMedia = () => Promise.resolve(new FakeStream([]));
    vi.stubGlobal('MediaStream', FakeStream);
    await renderCalls();
    await waitFor(() => expect(grabbed.calls).not.toBeNull());
    await act(async () => { await grabbed.calls!.startCall('pc-locked', 'Private', { private: true }); });
    await waitFor(() => expect(grabbed.calls!.call?.status).toBe('connected'));
    expect(grabbed.calls!.call?.private).toBe(true);
    expect(screen.getByTestId('call-private-status').textContent).toContain('Private chat call');
  });
});
