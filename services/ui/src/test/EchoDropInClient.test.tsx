import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { EchoDropInClient, fetchEchoDropInStatus, hangupEchoDropIn } from '../lib/echoDropInClient';
import FamilyCalls from '../components/family/FamilyCalls';
import { renderWithProviders } from './render';
import { server } from './setup';

// ---------------------------------------------------------------------------
// Fakes
// ---------------------------------------------------------------------------

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  readyState: number = WebSocket.CONNECTING;
  binaryType = '';
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  sent: Array<string | ArrayBuffer> = [];
  closeCode: number | null = null;
  closeReason = '';

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(data: string | ArrayBuffer | Blob | ArrayBufferView): void {
    this.sent.push(typeof data === 'string' ? data : (data as ArrayBuffer));
  }

  close(code = 1000, reason = ''): void {
    this.closeCode = code;
    this.closeReason = reason;
    this.readyState = WebSocket.CLOSED;
    this.onclose?.(new CloseEvent('close', { code, reason }));
  }

  simulateOpen(): void {
    this.readyState = WebSocket.OPEN;
    this.onopen?.(new Event('open'));
  }

  simulateMessage(data: string | ArrayBuffer): void {
    this.onmessage?.(new MessageEvent('message', { data }));
  }

  simulateClose(code = 1000): void {
    this.readyState = WebSocket.CLOSED;
    this.onclose?.(new CloseEvent('close', { code, reason: '' }));
  }
}

class MockAudioContext {
  state = 'running';
  sampleRate = 48_000;
  currentTime = 0;
  destination = {};
  createMediaStreamSource = vi.fn(() => ({ connect: vi.fn() }));
  createMediaStreamDestination = vi.fn(() => ({ stream: new MediaStream(), connect: vi.fn() }));
  createBuffer = vi.fn((channels: number, frames: number, sampleRate: number) => ({
    getChannelData: () => new Float32Array(frames),
    duration: frames / sampleRate,
  }));
  createBufferSource = vi.fn(() => ({ connect: vi.fn(), start: vi.fn(), onended: null, disconnect: vi.fn() }));
  resume = vi.fn(() => Promise.resolve());
  async close(): Promise<void> {}
}

class MockAudioData {
  format = 'f32-planar';
  sampleRate = 48_000;
  numberOfFrames: number;
  numberOfChannels = 1;
  timestamp: number;
  data: Float32Array;

  constructor(init: { numberOfFrames?: number; timestamp?: number; data?: Float32Array; sampleRate?: number } = {}) {
    this.numberOfFrames = init.numberOfFrames ?? 0;
    this.timestamp = init.timestamp ?? 0;
    this.sampleRate = init.sampleRate ?? 48_000;
    this.data = init.data ?? new Float32Array(this.numberOfFrames);
  }

  copyTo(dest: Float32Array, options?: { planeIndex?: number; format?: string }): void {
    const plane = options?.planeIndex ?? 0;
    const start = plane * this.numberOfFrames;
    dest.set(this.data.subarray(start, start + dest.length));
  }

  close(): void {}
}

class MockEncodedAudioChunk {
  type: string;
  timestamp: number;
  duration?: number;
  byteLength: number;
  private bytes: ArrayBuffer;

  constructor(init: { type?: string; timestamp: number; duration?: number; data: ArrayBuffer }) {
    this.type = init.type ?? 'key';
    this.timestamp = init.timestamp;
    this.duration = init.duration;
    this.bytes = init.data;
    this.byteLength = init.data.byteLength;
  }

  copyTo(dest: ArrayBuffer): void {
    new Uint8Array(dest).set(new Uint8Array(this.bytes));
  }
}

class MockAudioEncoder {
  static instances: MockAudioEncoder[] = [];
  state = 'unconfigured';
  output: (chunk: MockEncodedAudioChunk) => void;
  error: (error: Error) => void;
  encoded: MockEncodedAudioChunk[] = [];
  config: AudioEncoderConfig | null = null;

  constructor(init: { output: (chunk: MockEncodedAudioChunk) => void; error: (error: Error) => void }) {
    this.output = init.output;
    this.error = init.error;
    MockAudioEncoder.instances.push(this);
  }

  configure(config: AudioEncoderConfig): void {
    this.config = config;
    this.state = 'configured';
  }

  encode(data: MockAudioData): void {
    const chunk = new MockEncodedAudioChunk({
      type: 'key',
      timestamp: data.timestamp,
      data: new ArrayBuffer(40),
    });
    this.encoded.push(chunk);
    this.output(chunk);
  }

  close(): void {
    this.state = 'closed';
  }
}

class MockAudioDecoder {
  static instances: MockAudioDecoder[] = [];
  state = 'unconfigured';
  decodeQueueSize = 0;
  output: (data: MockAudioData) => void;
  error: (error: Error) => void;
  decoded: MockEncodedAudioChunk[] = [];
  config: AudioDecoderConfig | null = null;

  constructor(init: { output: (data: MockAudioData) => void; error: (error: Error) => void }) {
    this.output = init.output;
    this.error = init.error;
    MockAudioDecoder.instances.push(this);
  }

  configure(config: AudioDecoderConfig): void {
    this.config = config;
    this.state = 'configured';
  }

  decode(chunk: MockEncodedAudioChunk): void {
    this.decoded.push(chunk);
    const data = new MockAudioData({
      numberOfFrames: 960,
      timestamp: chunk.timestamp,
    });
    this.output(data);
  }

  close(): void {
    this.state = 'closed';
  }
}

class MockTrackReader {
  private processor: MockMediaStreamTrackProcessor;

  constructor(processor: MockMediaStreamTrackProcessor) {
    this.processor = processor;
  }

  read(): Promise<{ done: boolean; value?: MockAudioData }> {
    return this.processor.nextFrame();
  }

  cancel(): Promise<void> {
    this.processor.end();
    return Promise.resolve();
  }
}

class MockMediaStreamTrackProcessor {
  static instances: MockMediaStreamTrackProcessor[] = [];
  track: MediaStreamTrack;
  readable: { getReader: () => MockTrackReader };
  frames: MockAudioData[] = [];
  private waiters: Array<(frame: MockAudioData | null) => void> = [];
  private done = false;

  constructor(init: { track: MediaStreamTrack }) {
    this.track = init.track;
    MockMediaStreamTrackProcessor.instances.push(this);
    this.readable = { getReader: () => new MockTrackReader(this) };
  }

  pushFrame(frame: MockAudioData): void {
    const waiter = this.waiters.shift();
    if (waiter) waiter(frame);
    else this.frames.push(frame);
  }

  end(): void {
    this.done = true;
    for (const waiter of this.waiters.splice(0)) waiter(null);
  }

  nextFrame(): Promise<{ done: boolean; value?: MockAudioData }> {
    const frame = this.frames.shift();
    if (frame) return Promise.resolve({ done: false, value: frame });
    if (this.done) return Promise.resolve({ done: true, value: undefined });
    return new Promise((resolve) => {
      this.waiters.push((f) => {
        if (f) resolve({ done: false, value: f });
        else resolve({ done: true, value: undefined });
      });
    });
  }
}

function fakeMediaStream(muted = false): MediaStream {
  const track = {
    enabled: !muted,
    stop: vi.fn(),
    kind: 'audio',
  } as unknown as MediaStreamTrack;
  return {
    getAudioTracks: () => [track],
    getTracks: () => [track],
  } as unknown as MediaStream;
}

const API_KEY = 'test-token';
const origin = window.location.origin;

beforeEach(() => {
  MockWebSocket.instances = [];
  MockAudioEncoder.instances = [];
  MockAudioDecoder.instances = [];
  MockMediaStreamTrackProcessor.instances = [];
  vi.stubGlobal('WebSocket', MockWebSocket);
  vi.stubGlobal('AudioContext', MockAudioContext);
  vi.stubGlobal('webkitAudioContext', undefined);
  vi.stubGlobal('AudioEncoder', MockAudioEncoder);
  vi.stubGlobal('AudioDecoder', MockAudioDecoder);
  vi.stubGlobal('AudioData', MockAudioData);
  vi.stubGlobal('EncodedAudioChunk', MockEncodedAudioChunk);
  vi.stubGlobal('MediaStreamTrackProcessor', MockMediaStreamTrackProcessor);
  localStorage.setItem('jarvis_api_key', API_KEY);
});

afterEach(() => {
  vi.restoreAllMocks();
  MockWebSocket.instances = [];
  MockAudioEncoder.instances = [];
  MockAudioDecoder.instances = [];
  MockMediaStreamTrackProcessor.instances = [];
  vi.unstubAllGlobals();
  server.resetHandlers();
});

describe('fetchEchoDropInStatus / hangupEchoDropIn', () => {
  it('GETs the drop-in status with the bearer token', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    server.use(
      http.get('/api/homehub/echo_dropin/status', () =>
        HttpResponse.json({
          configured: true,
          active_sessions: [
            { node: 'crown', call_id: 'cafe', state: 'connected', peer_addr: { ip: '1.2.3.4', port: 5000 }, reason: '' },
          ],
          peers: [],
        }),
      ),
    );

    const status = await fetchEchoDropInStatus();
    expect(status.configured).toBe(true);
    expect(status.active_sessions).toHaveLength(1);
    expect(status.active_sessions?.[0].node).toBe('crown');

    expect(fetchMock).toHaveBeenCalled();
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(`${origin}/api/homehub/echo_dropin/status`);
    expect((init as { headers: { Authorization?: string } }).headers.Authorization).toBe(`Bearer ${API_KEY}`);
  });

  it('throws with the server detail on status failure', async () => {
    server.use(
      http.get('/api/homehub/echo_dropin/status', () => HttpResponse.json({ detail: 'Home Hub is offline' }, { status: 502 })),
    );
    await expect(fetchEchoDropInStatus()).rejects.toThrow(/Home Hub is offline/);
  });

  it('POSTs hangup to the call id with the bearer token', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    server.use(
      http.post('/api/homehub/echo_dropin/hangup/:callId', () =>
        HttpResponse.json({ call_id: 'cafe', state: 'ended', reason: 'hangup' }),
      ),
    );

    const result = await hangupEchoDropIn('cafe');
    expect(result).toEqual({ call_id: 'cafe', state: 'ended', reason: 'hangup' });

    const [url, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
    expect(String(url)).toBe(`${origin}/api/homehub/echo_dropin/hangup/cafe`);
    expect((init as { method?: string; headers?: { authorization?: string } }).method).toBe('POST');
    expect((init as { headers?: { Authorization?: string } }).headers?.Authorization).toBe(`Bearer ${API_KEY}`);
  });

  it('throws 404 for an unknown call_id', async () => {
    server.use(http.post('/api/homehub/echo_dropin/hangup/:callId', () => HttpResponse.json({ detail: 'Unknown call_id' }, { status: 404 })));
    await expect(hangupEchoDropIn('deadbeef')).rejects.toThrow(/Unknown call_id/);
  });
});

/** Connect a client and drive it to the connected state. */
async function connectClient(node = 'crown', callId = 'cafe1234'): Promise<{ ws: MockWebSocket; client: EchoDropInClient }> {
  const client = new EchoDropInClient({ node, token: API_KEY });
  const promise = client.connect();
  const ws = MockWebSocket.instances[0]!;
  ws.simulateOpen();
  ws.simulateMessage(JSON.stringify({ type: 'connected', call_id: callId, node }));
  await waitFor(() => expect(client.getState()).toBe('connected'));
  await promise;
  return { ws, client };
}

describe('EchoDropInClient lifecycle', () => {
  it('connects: opens the WS with the token, captures the mic, becomes connected', async () => {
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockResolvedValue(fakeMediaStream());

    const client = new EchoDropInClient({ node: 'crown', token: API_KEY });
    expect(client.getState()).toBe('idle');

    const promise = client.connect();

    const ws = MockWebSocket.instances[0];
    expect(ws.url).toBe(`${origin.replace(/^http/, 'ws')}/api/homehub/echo_dropin/ws/crown?token=${encodeURIComponent(API_KEY)}`);
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({ audio: true });

    ws.simulateOpen();
    ws.simulateMessage(JSON.stringify({ type: 'connected', call_id: 'cafe1234', node: 'crown' }));

    await waitFor(() => expect(client.getState()).toBe('connected'));
    await promise;
    expect(client.getCallId()).toBe('cafe1234');
    expect(client.getState()).toBe('connected');
  });

  it('transitions to error when the WS closes before connecting', async () => {
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockResolvedValue(fakeMediaStream());

    const onError = vi.fn();
    const client = new EchoDropInClient({ node: 'crown', token: API_KEY, onError });
    const promise = client.connect();

    MockWebSocket.instances[0]!.simulateClose();

    await waitFor(() => expect(client.getState()).toBe('error'));
    await promise;
    expect(onError).toHaveBeenCalled();
    expect(client.getState()).toBe('error');
  });

  it('rejects connect when microphone permission is denied', async () => {
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockRejectedValue(new Error('Not allowed'));

    const onError = vi.fn();
    const client = new EchoDropInClient({ node: 'crown', token: API_KEY, onError });
    await expect(client.connect()).rejects.toThrow(/Microphone access failed/);
    await waitFor(() => expect(client.getState()).toBe('error'));
    expect(onError).toHaveBeenCalledWith(expect.any(Error));
  });

  it('rejects connect when WebCodecs is unavailable', async () => {
    vi.stubGlobal('AudioEncoder', undefined);
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockResolvedValue(fakeMediaStream());

    const onError = vi.fn();
    const client = new EchoDropInClient({ node: 'crown', token: API_KEY, onError });

    await expect(client.connect()).rejects.toThrow(/This browser cannot send Drop In audio/);
    await waitFor(() => expect(client.getState()).toBe('error'));
    expect(onError).toHaveBeenCalledWith(expect.any(Error));
  });

  it('setMuted toggles the microphone track', async () => {
    const stream = fakeMediaStream(false);
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockResolvedValue(stream);

    const client = new EchoDropInClient({ node: 'crown', token: API_KEY });
    const promise = client.connect();
    MockWebSocket.instances[0]!.simulateOpen();
    MockWebSocket.instances[0]!.simulateMessage(JSON.stringify({ type: 'connected', call_id: 'cafe', node: 'crown' }));
    await waitFor(() => expect(client.getState()).toBe('connected'));
    await promise;

    const track = stream.getAudioTracks()[0];
    expect(track.enabled).toBe(true);

    client.setMuted(true);
    expect(client.isMuted()).toBe(true);
    expect(track.enabled).toBe(false);

    client.setMuted(false);
    expect(client.isMuted()).toBe(false);
    expect(track.enabled).toBe(true);
  });

  it('disconnect() tears down the WS, MediaStream tracks and AudioContext', async () => {
    const stream = fakeMediaStream(false);
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockResolvedValue(stream);
    const audioContextClose = vi.spyOn(MockAudioContext.prototype, 'close');

    const client = new EchoDropInClient({ node: 'crown', token: API_KEY });
    const promise = client.connect();
    const ws = MockWebSocket.instances[0]!;
    ws.simulateOpen();
    ws.simulateMessage(JSON.stringify({ type: 'connected', call_id: 'cafe', node: 'crown' }));
    await waitFor(() => expect(client.getState()).toBe('connected'));
    await promise;

    client.disconnect();

    expect(client.getState()).toBe('idle');
    expect(client.getCallId()).toBeNull();
    expect(ws.readyState).toBe(WebSocket.CLOSED);
    expect(ws.closeCode).toBe(1000);
    expect(stream.getTracks()[0].stop).toHaveBeenCalled();
    expect(audioContextClose).toHaveBeenCalled();
  });

  it('sends one binary Opus packet per encoded chunk', async () => {
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockResolvedValue(fakeMediaStream());

    const { ws } = await connectClient();

    const processor = MockMediaStreamTrackProcessor.instances[0]!;
    processor.pushFrame(new MockAudioData({ numberOfFrames: 960, timestamp: 0, data: new Float32Array(960).fill(0.25) }));
    processor.pushFrame(new MockAudioData({ numberOfFrames: 960, timestamp: 20_000, data: new Float32Array(960).fill(0.25) }));

    await waitFor(() => {
      const binaries = ws.sent.filter((m): m is ArrayBuffer => m instanceof ArrayBuffer);
      expect(binaries).toHaveLength(2);
    });

    // Each encoder chunk produced exactly one binary message.
    expect(MockAudioEncoder.instances[0]!.encoded).toHaveLength(2);
  });

  it('sends nothing while muted and resumes when unmuted', async () => {
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockResolvedValue(fakeMediaStream());

    const { ws, client } = await connectClient();
    const processor = MockMediaStreamTrackProcessor.instances[0]!;

    client.setMuted(true);
    processor.pushFrame(new MockAudioData({ numberOfFrames: 960, timestamp: 0, data: new Float32Array(960).fill(0.25) }));
    // Wait for the capture pump to consume the frame while muted.
    await waitFor(() => expect(processor.frames).toHaveLength(0));
    expect(ws.sent.filter((m) => m instanceof ArrayBuffer)).toHaveLength(0);

    client.setMuted(false);
    processor.pushFrame(new MockAudioData({ numberOfFrames: 960, timestamp: 20_000, data: new Float32Array(960).fill(0.25) }));
    await waitFor(() => expect(ws.sent.filter((m) => m instanceof ArrayBuffer)).toHaveLength(1));
  });

  it('never sends text frames to the server', async () => {
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockResolvedValue(fakeMediaStream());

    const { ws } = await connectClient();
    const processor = MockMediaStreamTrackProcessor.instances[0]!;
    processor.pushFrame(new MockAudioData({ numberOfFrames: 960, timestamp: 0, data: new Float32Array(960).fill(0.25) }));
    await waitFor(() => expect(ws.sent.filter((m) => m instanceof ArrayBuffer)).toHaveLength(1));

    expect(ws.sent).toHaveLength(1);
    expect(ws.sent[0]).toBeInstanceOf(ArrayBuffer);
  });

  it('sends nothing after the call ends', async () => {
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockResolvedValue(fakeMediaStream());

    const { ws, client } = await connectClient();
    const processor = MockMediaStreamTrackProcessor.instances[0]!;
    processor.pushFrame(new MockAudioData({ numberOfFrames: 960, timestamp: 0, data: new Float32Array(960).fill(0.25) }));
    await waitFor(() => expect(ws.sent.filter((m) => m instanceof ArrayBuffer)).toHaveLength(1));

    // Ending the call closes the socket; a late encoder frame must not send.
    client.disconnect();

    processor.pushFrame(new MockAudioData({ numberOfFrames: 960, timestamp: 20_000, data: new Float32Array(960).fill(0.25) }));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(ws.sent.filter((m) => m instanceof ArrayBuffer)).toHaveLength(1);
  });

  it('decodes incoming binary Opus packets', async () => {
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockResolvedValue(fakeMediaStream());

    const { ws } = await connectClient();
    const decoder = MockAudioDecoder.instances[0]!;

    ws.simulateMessage(new ArrayBuffer(80));
    expect(decoder.decoded).toHaveLength(1);

    ws.simulateMessage(new ArrayBuffer(80));
    expect(decoder.decoded).toHaveLength(2);
  });
});

type Entity = { entity_id: string; state: string; friendly_name?: string; attributes?: Record<string, unknown> };

const echoStates: Entity[] = [
  { entity_id: 'sensor.crown_drop_in', state: 'idle', friendly_name: 'Crown Drop In' },
  { entity_id: 'assist_satellite.crown', state: 'idle', friendly_name: 'Echo Show Crown' },
  { entity_id: 'input_boolean.crown_dnd', state: 'off' },
  { entity_id: 'sensor.donut_drop_in', state: 'idle', friendly_name: 'Donut Drop In' },
  { entity_id: 'assist_satellite.donut', state: 'unavailable', friendly_name: 'Kitchen Dot' },
  { entity_id: 'light.living_room', state: 'on' }, // unrelated: not an Echo
];

describe('FamilyCalls Echo rooms', () => {
  beforeEach(() => {
    vi.spyOn(globalThis, 'fetch');
    server.use(
      http.get('/api/entities', () => HttpResponse.json({ entities: echoStates })),
    );
  });

  it('lists Echo rooms with status badges and Drop In buttons', async () => {
    renderWithProviders(<FamilyCalls />);

    await waitFor(() => expect(screen.getByTestId('echo-room-crown')).toBeInTheDocument());

    // Crown: idle, online -> Ready, Drop In button
    expect(screen.getByText('Echo Show Crown')).toBeInTheDocument();
    expect(screen.getByTestId('echo-room-crown')).toHaveTextContent('Ready');
    expect(screen.getByRole('button', { name: 'Drop In on Echo Show Crown' })).toBeInTheDocument();

    // Donut: unavailable -> Offline, button disabled
    expect(screen.getByTestId('echo-room-donut')).toHaveTextContent('Offline');
    const donutButton = screen.getByRole('button', { name: /Kitchen Dot/ });
    expect(donutButton).toBeDisabled();
  });

  it('renders nothing extra when there are no Echo satellites', async () => {
    server.use(http.get('/api/entities', () => HttpResponse.json({ entities: [{ entity_id: 'light.living_room', state: 'on' }] })));
    renderWithProviders(<FamilyCalls />);

    await waitFor(() => expect(screen.queryByTestId('echo-room-crown')).toBeNull());
    expect(screen.queryByTestId('echo-room-donut')).toBeNull();
    expect(screen.getByTestId('family-calls')).toBeInTheDocument();
  });

  it('initiates a Drop-In through EchoDropInClient and shows Mute/End Call', async () => {
    const stream = fakeMediaStream(false);
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockResolvedValue(stream);

    const connectSpy = vi.spyOn(EchoDropInClient.prototype, 'connect');
    const setMutedSpy = vi.spyOn(EchoDropInClient.prototype, 'setMuted');
    const disconnectSpy = vi.spyOn(EchoDropInClient.prototype, 'disconnect');

    const user = userEvent.setup();
    const { container } = renderWithProviders(<FamilyCalls />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Drop In on Echo Show Crown' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Drop In on Echo Show Crown' }));

    await waitFor(() => expect(connectSpy).toHaveBeenCalledTimes(1));

    // Simulate the Drop-In session becoming connected (server sends the 'connected' frame).
    const ws = MockWebSocket.instances[0];
    act(() => {
      ws.simulateOpen();
      ws.simulateMessage(JSON.stringify({ type: 'connected', call_id: 'cafe', node: 'crown' }));
    });

    // Now connected: Mute + End Call buttons show
    await waitFor(() => expect(container.innerHTML).toContain('End Call'));
    await waitFor(() => expect(container.innerHTML).toContain('Mute microphone'));

    const muteBtn = screen.getByRole('button', { name: /Mute microphone/ });
    await user.click(muteBtn);
    expect(setMutedSpy).toHaveBeenCalledWith(true);

    await user.click(screen.getByText('End Call'));
    expect(disconnectSpy).toHaveBeenCalled();
  });
});

describe('resampleTo48k', () => {
  it('keeps 48 kHz input as it is and stretches 16 kHz input to 48 kHz', async () => {
    const { resampleTo48k } = await import('../lib/echoDropInClient');
    const same = new Float32Array([0.1, 0.2, 0.3]);
    expect(resampleTo48k(same, 48_000)).toBe(same);
    const out = resampleTo48k(new Float32Array(320), 16_000);
    expect(out.length).toBe(960);
    const ramp = resampleTo48k(new Float32Array([0, 1]), 24_000);
    expect(ramp.length).toBe(4);
    expect(ramp[1]).toBeCloseTo(0.5);
  });
});
