/**
 * Echo Drop-In client for the Phone/Web <-> Echo Dot Drop-In integration.
 *
 * Provides HTTP helpers for drop-in status/hangup and a WebSocket audio client
 * that bridges the browser microphone to a Home Hub echo drop-in session.
 *
 * The wire format is one raw Opus packet per binary WebSocket message. Home Hub
 * treats any text frame from the client as the end of the stream, so mute stops
 * sending packets and ending the call closes the socket with code 1000. This
 * module never logs audio payloads or the auth token.
 */

import { directRequestConfig } from '../services/api';

export type EchoDropInClientState = 'idle' | 'connecting' | 'connected' | 'error';

export interface EchoDropInSessionStatus {
  configured?: boolean;
  active_sessions?: Array<{
    node: string;
    call_id: string;
    state: string;
    peer_addr?: { ip: string; port: number };
    reason?: string;
  }>;
  peers?: Array<{ node: string; peer_id: string; ip: string; port: number }>;
}

const DROP_IN_BASE = '/api/homehub/echo_dropin';

/** The bridge expects mono 48 kHz Opus, one 20 ms packet per message. */
const OPUS_SAMPLE_RATE = 48_000;
const OPUS_CHANNELS = 1;
const OPUS_BITRATE = 32_000;
const FRAME_SAMPLES = 960;
const FRAME_TIMESTAMP_US = 20_000;

/** Incoming audio is held this long before playback to absorb network jitter. */
const PLAYBACK_JITTER_S = 0.06;
/** Beyond this many queued packets, drop the oldest instead of growing latency. */
const MAX_DECODE_QUEUE = 50;

const CAPTURE_UNSUPPORTED = 'This browser cannot send Drop In audio';

function toWsUrl(baseURL: string): string {
  if (baseURL.startsWith('https://')) return 'wss://' + baseURL.slice(8);
  if (baseURL.startsWith('http://')) return 'ws://' + baseURL.slice(7);
  return baseURL;
}

function serverMessage(detail: unknown, fallback: string): string {
  const body = detail as { detail?: string; message?: string } | undefined;
  if (body && typeof body.detail === 'string' && body.detail.trim()) return body.detail;
  if (body && typeof body.message === 'string' && body.message.trim()) return body.message;
  return fallback;
}

/** GET /api/homehub/echo_dropin/status */
export async function fetchEchoDropInStatus(): Promise<EchoDropInSessionStatus> {
  const { baseURL, headers } = directRequestConfig();
  const resp = await fetch(`${baseURL}${DROP_IN_BASE}/status`, { headers });
  if (!resp.ok) {
    let detail: unknown;
    try {
      detail = await resp.json();
    } catch {
      detail = undefined;
    }
    throw new Error(serverMessage(detail, `Drop-In status failed (${resp.status})`));
  }
  return (await resp.json()) as EchoDropInSessionStatus;
}

/** POST /api/homehub/echo_dropin/hangup/{call_id} */
export async function hangupEchoDropIn(callId: string): Promise<{
  call_id: string;
  state: string;
  reason: string;
}> {
  const { baseURL, headers } = directRequestConfig();
  const resp = await fetch(`${baseURL}${DROP_IN_BASE}/hangup/${encodeURIComponent(callId)}`, {
    method: 'POST',
    headers,
  });
  if (!resp.ok) {
    let detail: unknown;
    try {
      detail = await resp.json();
    } catch {
      detail = undefined;
    }
    throw new Error(serverMessage(detail, `Hangup failed (${resp.status})`));
  }
  return (await resp.json()) as { call_id: string; state: string; reason: string };
}

type AudioEncoderCtor = new (init: AudioEncoderInit) => AudioEncoder;
type AudioDecoderCtor = new (init: AudioDecoderInit) => AudioDecoder;

interface TrackProcessorInit {
  track: MediaStreamTrack;
}

interface TrackProcessorLike {
  readable: Readonly<{ getReader: () => TrackProcessorReader }>;
}

interface TrackProcessorReader {
  read: () => Promise<{ done: boolean; value?: AudioData }>;
  cancel: () => Promise<void>;
}

type TrackProcessorCtor = new (init: TrackProcessorInit) => TrackProcessorLike;

/** The worklet posts one 20 ms mono frame at a time to the main thread. */
const CAPTURE_WORKLET = `
class DropInCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = new Float32Array(${FRAME_SAMPLES});
    this.offset = 0;
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      for (let i = 0; i < channel.length; i += 1) {
        this.samples[this.offset] = channel[i];
        this.offset += 1;
        if (this.offset === this.samples.length) {
          this.port.postMessage(this.samples);
          this.samples = new Float32Array(${FRAME_SAMPLES});
          this.offset = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor('drop-in-capture', DropInCaptureProcessor);
`;

export interface EchoDropInClientOptions {
  node: string;
  token: string;
  onStateChange?: (state: EchoDropInClientState) => void;
  onError?: (error: Error) => void;
}

/**
 * One live Drop-In session to an Echo node.
 *
 * Opens WS /api/homehub/echo_dropin/ws/{node}?token=<api key>, encodes the
 * microphone with WebCodecs into 20 ms Opus packets, streams each packet as one
 * binary message, decodes incoming Opus packets with AudioDecoder, and cleanly
 * tears down the WebSocket, MediaStream tracks and AudioContext on disconnect().
 */
export class EchoDropInClient {
  private readonly node: string;
  private readonly token: string;
  private readonly onStateChange?: (state: EchoDropInClientState) => void;
  private readonly onError?: (error: Error) => void;

  private ws: WebSocket | null = null;
  private mediaStream: MediaStream | null = null;
  private audioTrack: MediaStreamTrack | null = null;
  private audioContext: AudioContext | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private captureNode: AudioWorkletNode | null = null;
  private captureGain: GainNode | null = null;
  private captureReader: TrackProcessorReader | null = null;
  private processorCtor: TrackProcessorCtor | null = null;
  private encoder: AudioEncoder | null = null;
  private decoder: AudioDecoder | null = null;
  private playSources = new Set<AudioBufferSourceNode>();

  private state: EchoDropInClientState = 'idle';
  private callId: string | null = null;
  private muted = false;
  private captureStarted = false;
  private captureSamples = new Float32Array(0);
  private captureTimestamp = 0;
  private playbackTimestamp = 0;
  private nextPlayTime: number | null = null;

  constructor(options: EchoDropInClientOptions) {
    this.node = options.node;
    this.token = options.token;
    this.onStateChange = options.onStateChange;
    this.onError = options.onError;
  }

  getState(): EchoDropInClientState {
    return this.state;
  }

  getCallId(): string | null {
    return this.callId;
  }

  isMuted(): boolean {
    return this.muted;
  }

  private setState(next: EchoDropInClientState): void {
    if (this.state === next) return;
    this.state = next;
    this.onStateChange?.(next);
  }

  private fail(error: Error): void {
    if (this.state === 'error') return;
    this.teardown();
    this.setState('error');
    this.onError?.(error);
  }

  private wsUrl(): string {
    const { baseURL } = directRequestConfig();
    return `${toWsUrl(baseURL)}${DROP_IN_BASE}/ws/${encodeURIComponent(this.node)}?token=${encodeURIComponent(this.token)}`;
  }

  async connect(): Promise<void> {
    if (this.state === 'connecting' || this.state === 'connected') return;
    this.setState('connecting');

    // Fail before opening a session when this browser cannot send audio: a live
    // Drop-In on the Echo with no outgoing media is worse than a clear error.
    const Encoder = audioEncoderCtor();
    const Processor = trackProcessorCtor();
    if (!Encoder || (!Processor && typeof AudioWorkletNode === 'undefined')) {
      const failure = new Error(CAPTURE_UNSUPPORTED);
      this.fail(failure);
      throw failure;
    }
    this.processorCtor = Processor;

    let ws: WebSocket;
    try {
      ws = new WebSocket(this.wsUrl());
    } catch (error) {
      const err = error instanceof Error ? error : new Error(String(error));
      this.fail(new Error(`Could not open Drop-In WebSocket: ${err.message}`));
      return;
    }
    this.ws = ws;
    ws.binaryType = 'arraybuffer';

    const onClose = () => {
      // Ignore a stale socket (an intentional close, or one already replaced).
      if (this.ws !== ws) return;
      this.ws = null;
      this.teardownMedia();
      if (this.state === 'connecting' || this.state === 'connected') {
        this.setState('error');
        this.onError?.(new Error('Drop-In WebSocket closed'));
      } else {
        this.setState('idle');
      }
    };

    ws.onopen = () => {
      // Waiting for the server's { type: 'connected', call_id } frame.
    };

    ws.onmessage = (event: MessageEvent) => {
      if (this.ws !== ws) return;
      if (typeof event.data === 'string') {
        try {
          const message = JSON.parse(event.data);
          if (message && message.type === 'connected' && typeof message.call_id === 'string') {
            this.callId = message.call_id;
            this.setState('connected');
            this.startSending();
          }
        } catch {
          // Ignore non-JSON text frames.
        }
        return;
      }
      this.playAudioFrame(event.data as ArrayBuffer);
    };

    ws.onerror = () => {
      if (this.ws !== ws) return;
      this.fail(new Error('Drop-In WebSocket error'));
    };

    ws.onclose = onClose;

    try {
      const encoder = new Encoder({
        output: (chunk) => this.sendEncodedChunk(chunk),
        error: () => {
          this.fail(new Error('Drop-In audio encoder failed'));
        },
      });
      encoder.configure({
        codec: 'opus',
        sampleRate: OPUS_SAMPLE_RATE,
        numberOfChannels: OPUS_CHANNELS,
        bitrate: OPUS_BITRATE,
      });
      this.encoder = encoder;

      this.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });

      // The socket may have closed (or the caller may have disconnected) while
      // we were waiting for the microphone. A teardown already ran, so release
      // the mic instead of keeping it and a new AudioContext alive for a session
      // that no longer exists.
      if (this.state === 'error' || this.state === 'idle') {
        for (const track of this.mediaStream.getTracks()) {
          try {
            track.stop();
          } catch {
            // ignore
          }
        }
        this.mediaStream = null;
        return;
      }

      const tracks = this.mediaStream.getAudioTracks();
      this.audioTrack = tracks[0] ?? null;
      if (!this.audioTrack) {
        throw new Error('No audio track available from the microphone');
      }
      this.applyMuted();

      const AudioContextCtor =
        window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AudioContextCtor) {
        throw new Error('AudioContext is not available');
      }
      const context = new AudioContextCtor({ sampleRate: OPUS_SAMPLE_RATE });
      this.audioContext = context;
      if (context.state === 'suspended') {
        await context.resume();
      }

      this.startDecoder();
      this.startSending();
    } catch (error) {
      const err = error instanceof Error ? error : new Error(String(error));
      const failure = err.message === CAPTURE_UNSUPPORTED
        ? err
        : new Error(`Microphone access failed: ${err.message}`);
      this.fail(failure);
      throw failure;
    }
  }

  private startDecoder(): void {
    const Decoder = audioDecoderCtor();
    if (!Decoder) return;
    try {
      const decoder = new Decoder({
        output: (data) => this.schedulePlayback(data),
        error: () => {
          this.decoder = null;
        },
      });
      decoder.configure({ codec: 'opus', sampleRate: OPUS_SAMPLE_RATE, numberOfChannels: OPUS_CHANNELS });
      this.decoder = decoder;
    } catch {
      this.decoder = null;
    }
  }

  private startSending(): void {
    if (this.state !== 'connected' || this.captureStarted) return;
    const track = this.audioTrack;
    const context = this.audioContext;
    if (!track || !context) return;
    this.captureStarted = true;

    const Processor = this.processorCtor;
    if (Processor) {
      this.startTrackProcessor(track, Processor);
      return;
    }
    void this.startWorklet(track, context);
  }

  private startTrackProcessor(track: MediaStreamTrack, Processor: TrackProcessorCtor): void {
    try {
      const processor = new Processor({ track });
      const reader = processor.readable.getReader();
      this.captureReader = reader;
      const pump = async () => {
        try {
          for (;;) {
            const { done, value } = await reader.read();
            if (done) return;
            if (value) this.consumeAudioData(value);
          }
        } catch {
          // The track ended or the reader was cancelled. Nothing to log.
        }
      };
      void pump();
    } catch {
      this.fail(new Error('Drop-In microphone capture could not start'));
    }
  }

  private async startWorklet(track: MediaStreamTrack, context: AudioContext): Promise<void> {
    try {
      const source = context.createMediaStreamSource(this.mediaStream ?? new MediaStream([track]));
      this.sourceNode = source;

      const moduleUrl = URL.createObjectURL(new Blob([CAPTURE_WORKLET], { type: 'application/javascript' }));
      try {
        await context.audioWorklet.addModule(moduleUrl);
      } finally {
        URL.revokeObjectURL(moduleUrl);
      }
      if (this.state !== 'connected') return;

      const node = new AudioWorkletNode(context, 'drop-in-capture');
      node.port.onmessage = (event: MessageEvent) => {
        this.queueSamples(event.data as Float32Array);
      };
      // The node has no audible output; the silent gain keeps it processing.
      const silent = context.createGain();
      silent.gain.value = 0;
      node.connect(silent);
      silent.connect(context.destination);
      source.connect(node);
      this.captureNode = node;
      this.captureGain = silent;
    } catch {
      this.fail(new Error('Drop-In microphone capture could not start'));
    }
  }

  /** Read one AudioData frame from the track and hand its samples to the encoder. */
  private consumeAudioData(frame: AudioData): void {
    try {
      if (this.state !== 'connected' || this.muted) return;
      const samples = new Float32Array(frame.numberOfFrames);
      frame.copyTo(samples, { planeIndex: 0, format: 'f32-planar' });
      // The track runs at the mic's own rate; Opus frames here are 48 kHz.
      this.queueSamples(resampleTo48k(samples, frame.sampleRate));
    } catch {
      // A frame we cannot read is dropped.
    } finally {
      try {
        frame.close();
      } catch {
        // ignore
      }
    }
  }

  /** Buffer incoming samples and encode them as 20 ms frames. */
  private queueSamples(samples: Float32Array): void {
    if (this.state !== 'connected' || this.muted || samples.length === 0) return;
    const buffered = new Float32Array(this.captureSamples.length + samples.length);
    buffered.set(this.captureSamples, 0);
    buffered.set(samples, this.captureSamples.length);

    let offset = 0;
    while (buffered.length - offset >= FRAME_SAMPLES) {
      this.encodeFrame(buffered.slice(offset, offset + FRAME_SAMPLES));
      offset += FRAME_SAMPLES;
    }
    this.captureSamples = buffered.slice(offset);
  }

  private encodeFrame(samples: Float32Array): void {
    const encoder = this.encoder;
    if (!encoder || this.state !== 'connected' || this.muted) return;
    let data: AudioData | null = null;
    try {
      data = new AudioData({
        format: 'f32-planar',
        sampleRate: OPUS_SAMPLE_RATE,
        numberOfFrames: samples.length,
        numberOfChannels: OPUS_CHANNELS,
        timestamp: this.captureTimestamp,
        data: samples as Float32Array<ArrayBuffer>,
      });
      this.captureTimestamp += FRAME_TIMESTAMP_US;
      encoder.encode(data);
    } catch {
      // A frame the encoder rejects is dropped.
    } finally {
      if (data) {
        try {
          data.close();
        } catch {
          // ignore
        }
      }
    }
  }

  /** Send one Opus packet per encoded chunk. Never called with a string. */
  private sendEncodedChunk(chunk: EncodedAudioChunk): void {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN || this.muted || this.state !== 'connected') return;
    try {
      const payload = new ArrayBuffer(chunk.byteLength);
      chunk.copyTo(payload);
      ws.send(payload);
    } catch {
      // A failed send ends nothing; the socket close will surface the error.
    }
  }

  /** Play an incoming binary Opus packet. Never uses decodeAudioData. */
  private playAudioFrame(data: ArrayBuffer | ArrayBufferView): void {
    const decoder = this.decoder;
    if (!decoder || this.state !== 'connected' || decoder.state !== 'configured') return;
    if (decoder.decodeQueueSize > MAX_DECODE_QUEUE) return;

    let bytes: ArrayBuffer;
    if (data instanceof ArrayBuffer) {
      bytes = data;
    } else if (ArrayBuffer.isView(data)) {
      const view = data as ArrayBufferView;
      bytes = view.buffer.slice(view.byteOffset, view.byteOffset + view.byteLength) as ArrayBuffer;
    } else {
      return;
    }
    if (bytes.byteLength === 0) return;

    try {
      const chunk = new EncodedAudioChunk({
        type: 'key',
        timestamp: this.playbackTimestamp,
        data: bytes,
      });
      this.playbackTimestamp += FRAME_TIMESTAMP_US;
      decoder.decode(chunk);
    } catch {
      // An undecodable packet is dropped without logging audio.
    }
  }

  /** Schedule decoded PCM on the AudioContext with a small jitter buffer. */
  private schedulePlayback(audio: AudioData): void {
    const context = this.audioContext;
    try {
      if (!context || this.state !== 'connected' || context.state === 'closed') return;
      if (audio.numberOfFrames === 0) return;
      const buffer = context.createBuffer(audio.numberOfChannels, audio.numberOfFrames, audio.sampleRate);
      for (let channel = 0; channel < audio.numberOfChannels; channel += 1) {
        const target = buffer.getChannelData(channel);
        audio.copyTo(target, { planeIndex: channel, format: 'f32-planar' });
      }

      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(context.destination);

      const now = context.currentTime;
      if (this.nextPlayTime === null || this.nextPlayTime < now) {
        this.nextPlayTime = now + PLAYBACK_JITTER_S;
      }
      const when = this.nextPlayTime;
      this.nextPlayTime = when + buffer.duration;

      this.playSources.add(source);
      source.onended = () => {
        this.playSources.delete(source);
        try {
          source.disconnect();
        } catch {
          // ignore
        }
      };
      source.start(when);
    } catch {
      // Dropped playback. Never log audio.
    } finally {
      try {
        audio.close();
      } catch {
        // ignore
      }
    }
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    this.applyMuted();
  }

  private applyMuted(): void {
    if (this.audioTrack) {
      this.audioTrack.enabled = !this.muted;
    }
  }

  private teardownMedia(): void {
    if (this.captureReader) {
      try {
        void this.captureReader.cancel();
      } catch {
        // ignore
      }
      this.captureReader = null;
    }
    if (this.captureNode) {
      try {
        this.captureNode.disconnect();
      } catch {
        // ignore
      }
      this.captureNode = null;
    }
    if (this.captureGain) {
      try {
        this.captureGain.disconnect();
      } catch {
        // ignore
      }
      this.captureGain = null;
    }
    if (this.sourceNode) {
      try {
        this.sourceNode.disconnect();
      } catch {
        // ignore
      }
      this.sourceNode = null;
    }
    if (this.encoder) {
      try {
        if (this.encoder.state !== 'closed') this.encoder.close();
      } catch {
        // ignore
      }
      this.encoder = null;
    }
    if (this.decoder) {
      try {
        if (this.decoder.state !== 'closed') this.decoder.close();
      } catch {
        // ignore
      }
      this.decoder = null;
    }
    for (const source of this.playSources) {
      try {
        source.stop();
      } catch {
        // ignore
      }
      try {
        source.disconnect();
      } catch {
        // ignore
      }
    }
    this.playSources.clear();
    this.nextPlayTime = null;
    this.captureStarted = false;
    this.captureSamples = new Float32Array(0);
    this.captureTimestamp = 0;
    this.playbackTimestamp = 0;

    if (this.audioTrack) {
      try {
        this.audioTrack.stop();
      } catch {
        // ignore
      }
      this.audioTrack = null;
    }
    if (this.mediaStream) {
      for (const track of this.mediaStream.getTracks()) {
        try {
          track.stop();
        } catch {
          // ignore
        }
      }
      this.mediaStream = null;
    }
    if (this.audioContext) {
      try {
        this.audioContext.close();
      } catch {
        // ignore
      }
      this.audioContext = null;
    }
  }

  private teardown(): void {
    this.teardownMedia();
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      try {
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
          ws.close(1000, 'client ended');
        }
      } catch {
        // ignore
      }
    }
    this.callId = null;
  }

  disconnect(): void {
    this.teardown();
    this.setState('idle');
  }
}

function audioEncoderCtor(): AudioEncoderCtor | null {
  const ctor = (globalThis as { AudioEncoder?: AudioEncoderCtor }).AudioEncoder;
  return typeof ctor === 'function' ? ctor : null;
}

function audioDecoderCtor(): AudioDecoderCtor | null {
  const ctor = (globalThis as { AudioDecoder?: AudioDecoderCtor }).AudioDecoder;
  return typeof ctor === 'function' ? ctor : null;
}

function trackProcessorCtor(): TrackProcessorCtor | null {
  const ctor = (globalThis as { MediaStreamTrackProcessor?: TrackProcessorCtor }).MediaStreamTrackProcessor;
  return typeof ctor === 'function' ? ctor : null;
}

/** Linear resample of mono samples to the Opus rate; returns the input when it is already 48 kHz. */
export function resampleTo48k(samples: Float32Array, fromRate: number): Float32Array {
  if (!fromRate || fromRate === OPUS_SAMPLE_RATE || samples.length === 0) return samples;
  const outLength = Math.round((samples.length * OPUS_SAMPLE_RATE) / fromRate);
  const out = new Float32Array(outLength);
  const step = fromRate / OPUS_SAMPLE_RATE;
  for (let i = 0; i < outLength; i += 1) {
    const pos = i * step;
    const left = Math.floor(pos);
    const right = Math.min(left + 1, samples.length - 1);
    const frac = pos - left;
    out[i] = samples[left] * (1 - frac) + samples[right] * frac;
  }
  return out;
}
