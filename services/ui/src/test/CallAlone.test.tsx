import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import { CallProvider } from '../context/CallContext';

/**
 * A phone that joined someone else's call never stays in an empty room with its microphone open:
 * a Drop In whose caller has gone leaves at once (and rings nobody), and once everyone else has left
 * a call the phone hangs up after a short wait.
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

const rang = vi.hoisted(() => ({ calls: [] as string[], signedIn: false }));
vi.mock('../services/api', async (original) => {
  const real = await original<typeof import('../services/api')>();
  return {
    ...real,
    api: {
      ...real.api,
      ringCall: (room: string) => { rang.calls.push(room); return Promise.resolve({ ringing: [] }); },
      getCallList: () => Promise.resolve({ calls: [], deviceDropIn: true }),
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

function renderCalls() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AuthProvider>
          <CallProvider><div /></CallProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('a phone never stays alone in a call it did not start', () => {
  beforeEach(() => {
    FakeSocket.last = null;
    localStorage.setItem('jarvis_api_key', 'test-key');
    localStorage.setItem('jarvis_user', JSON.stringify({ username: 'mom', is_admin: true }));
    rang.calls = [];
    rang.signedIn = false;
    native.ended.mockReset();
    vi.stubGlobal('WebSocket', FakeSocket);
    vi.stubGlobal('RTCPeerConnection', FakePeerConnection);
    const track = { stop: vi.fn(), enabled: true, kind: 'audio' };
    const stream = { getTracks: () => [track], getAudioTracks: () => [track], getVideoTracks: () => [] };
    const devices = navigator.mediaDevices as unknown as Record<string, unknown> | undefined;
    if (devices) devices.getUserMedia = vi.fn().mockResolvedValue(stream);
    else Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: vi.fn().mockResolvedValue(stream) } });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
    localStorage.clear();
  });

  it('leaves a Drop In at once when its caller has already gone, ringing nobody', async () => {
    FakeSocket.welcomePeers = [];
    renderCalls();
    await waitFor(() => expect(native.onJoin).toBeTypeOf('function'));
    await waitFor(() => expect(rang.signedIn).toBe(true));
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });  // the session is in place
    act(() => native.onJoin?.('room-1', 'Dad'));
    await waitFor(() => expect(FakeSocket.last?.closed).toBe(true));
    expect(rang.calls).toEqual([]);
  });

  it('hangs up 15 s after everyone else has left', async () => {
    FakeSocket.welcomePeers = [{ id: 'dad-1', username: 'dad', name: 'Dad', muted: false, camera: false }];
    renderCalls();
    await waitFor(() => expect(native.onJoin).toBeTypeOf('function'));
    await waitFor(() => expect(rang.signedIn).toBe(true));
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    act(() => native.onJoin?.('room-1', 'Dad'));
    await waitFor(() => expect(FakeSocket.last?.sent.length).toBeGreaterThan(0));
    vi.useFakeTimers();
    act(() => FakeSocket.last?.receive({ type: 'peer-left', from: 'dad-1', id: 'dad-1' }));
    expect(FakeSocket.last?.closed).toBe(false);
    await act(async () => { vi.advanceTimersByTime(14_000); });
    expect(FakeSocket.last?.closed).toBe(false);
    await act(async () => { vi.advanceTimersByTime(2_000); });
    expect(FakeSocket.last?.closed).toBe(true);
  });
});
