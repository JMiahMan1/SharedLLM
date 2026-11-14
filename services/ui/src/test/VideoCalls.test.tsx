import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import { CallProvider, useCall } from '../context/CallContext';

/**
 * Video calls: one started as video rings as video with the camera on; a video Drop In shows the
 * camera only on a device switched to "camera on for a video Drop In", and connects with the
 * microphone alone everywhere else.
 */

const native = vi.hoisted(() => ({
  onJoin: null as null | ((room: string, name: string) => void),
  ended: vi.fn(),
}));

vi.mock('../lib/dropIn', () => ({
  listenForDropIn: (onJoin: (room: string, name: string) => void) => {
    native.onJoin = onJoin;
    return () => {};
  },
  reportDropInEnded: native.ended,
  reportDropInJoined: vi.fn(),
  nativeDropInStatus: () => Promise.resolve({ overOtherApps: true }),
}));

const rang = vi.hoisted(() => ({
  calls: [] as { room: string; video?: boolean }[],
  signedIn: false,
  list: [] as unknown[],
  videoDropIn: false,
}));
vi.mock('../services/api', async (original) => {
  const real = await original<typeof import('../services/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      ringCall: (room: string, opts: { video?: boolean } = {}) => {
        rang.calls.push({ room, video: opts.video });
        return Promise.resolve({ ringing: ['dad'] });
      },
      getCallList: () => Promise.resolve({ calls: rang.list, deviceDropIn: true, deviceDropInVideo: rang.videoDropIn }),
      getFamilyDirectory: () => Promise.resolve([]),
      getMe: () => { rang.signedIn = true; return Promise.resolve({ username: 'mom', is_admin: true }); },
    },
  };
});

class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static last: FakeSocket | null = null;
  static welcomePeers: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  readyState = 0;
  closed = false;
  sent: string[] = [];
  url: string;
  constructor(url: string) {
    this.url = url;
    FakeSocket.last = this;
    setTimeout(() => {
      this.readyState = 1;
      this.onopen?.();
      this.receive({ type: 'welcome', self: { id: 'me' }, peers: FakeSocket.welcomePeers, ice_servers: [], room: 'room-1' });
    }, 0);
  }
  receive(message: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
  send(data: string) { this.sent.push(data); }
  close() { this.closed = true; this.readyState = 3; }
}

class FakePeerConnection {
  signalingState = 'stable';
  connectionState = 'new';
  localDescription = null;
  onicecandidate = null;
  ontrack = null;
  onnegotiationneeded = null;
  onconnectionstatechange = null;
  addTrack() { return {}; }
  getSenders() { return []; }
  getTransceivers() { return []; }
  addTransceiver() { return {}; }
  setLocalDescription() { return Promise.resolve(); }
  setRemoteDescription() { return Promise.resolve(); }
  addIceCandidate() { return Promise.resolve(); }
  close() {}
}



/** A media stream as a browser's behaves: tracks can be added and listed by kind. */
class FakeStream {
  tracks: { kind: string; stop: () => void; enabled: boolean }[];
  constructor(tracks: { kind: string; stop: () => void; enabled: boolean }[] = []) { this.tracks = [...tracks]; }
  addTrack(track: { kind: string; stop: () => void; enabled: boolean }) { this.tracks.push(track); }
  removeTrack(track: unknown) { this.tracks = this.tracks.filter((t) => t !== track); }
  getTracks() { return [...this.tracks]; }
  getAudioTracks() { return this.tracks.filter((t) => t.kind === 'audio'); }
  getVideoTracks() { return this.tracks.filter((t) => t.kind === 'video'); }
}

let calls: ReturnType<typeof useCall> | null = null;
function Grab() {
  calls = useCall();
  return null;
}

function renderCalls() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AuthProvider>
          <CallProvider><Grab /></CallProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const asked: { video: boolean }[] = [];

describe('video calls', () => {
  beforeEach(() => {
    FakeSocket.last = null;
    FakeSocket.welcomePeers = [{ id: 'dad-1', username: 'dad', name: 'Dad', muted: false, camera: true }];
    calls = null;
    asked.length = 0;
    localStorage.setItem('jarvis_api_key', 'test-key');
    localStorage.setItem('jarvis_user', JSON.stringify({ username: 'mom', is_admin: true }));
    rang.calls = [];
    rang.signedIn = false;
    rang.list = [];
    rang.videoDropIn = false;
    vi.stubGlobal('WebSocket', FakeSocket);
    vi.stubGlobal('RTCPeerConnection', FakePeerConnection);
    const getUserMedia = vi.fn((constraints: { video?: unknown }) => {
      asked.push({ video: Boolean(constraints.video) });
      const track = { stop: vi.fn(), enabled: true, kind: constraints.video ? 'video' : 'audio' };
      return Promise.resolve(new FakeStream([track]));
    });
    vi.stubGlobal('MediaStream', FakeStream);
    // jsdom checks a video element's stream is a real MediaStream; showing it is not what is tested here.
    Object.defineProperty(HTMLMediaElement.prototype, 'srcObject', { configurable: true, get: () => null, set: () => {} });
    const devices = navigator.mediaDevices as unknown as Record<string, unknown> | undefined;
    if (devices) devices.getUserMedia = getUserMedia;
    else Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  async function ready() {
    renderCalls();
    await waitFor(() => expect(native.onJoin).toBeTypeOf('function'));
    await waitFor(() => expect(rang.signedIn).toBe(true));
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  }

  it('a call started as video rings as video and turns the camera on', async () => {
    FakeSocket.welcomePeers = [];
    await ready();
    await act(async () => { await calls!.startCall('room-1', 'Dad', { video: true }); });
    await waitFor(() => expect(rang.calls).toEqual([{ room: 'room-1', video: true }]));
    await waitFor(() => expect(calls!.cameraOn).toBe(true));
    expect(asked.some((a) => a.video)).toBe(true);
  });

  it('an ordinary call rings as audio and leaves the camera off', async () => {
    FakeSocket.welcomePeers = [];
    await ready();
    await act(async () => { await calls!.startCall('room-1', 'Dad'); });
    await waitFor(() => expect(rang.calls).toEqual([{ room: 'room-1', video: undefined }]));
    expect(calls!.cameraOn).toBe(false);
    expect(asked.some((a) => a.video)).toBe(false);
  });

  it('a video Drop In shows the camera on a device switched to video', async () => {
    rang.videoDropIn = true;
    rang.list = [{ token: 'room-1', name: 'Dad', started_at: 1, participants: [], ringing: true, drop_in: true, video: true }];
    await ready();
    await waitFor(() => expect(FakeSocket.last).not.toBeNull());
    await waitFor(() => expect(calls!.cameraOn).toBe(true));
  });

  it('a video Drop In on a device not switched to video connects with the microphone only', async () => {
    rang.videoDropIn = false;
    rang.list = [{ token: 'room-1', name: 'Dad', started_at: 1, participants: [], ringing: true, drop_in: true, video: true }];
    await ready();
    await waitFor(() => expect(FakeSocket.last?.sent.length ?? 0).toBeGreaterThan(0));
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    expect(calls!.cameraOn).toBe(false);
    expect(asked.some((a) => a.video)).toBe(false);
  });
});
