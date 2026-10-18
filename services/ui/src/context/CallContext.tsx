import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Capacitor } from '@capacitor/core';
import { Loader2, Mic, MicOff, Phone, PhoneOff, Video, VideoOff, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import type { ActiveCall, CallParticipant } from '../types/api';
import { useAuth } from './AuthContext';
import { storageGetSync } from '../lib/storage';

/** How often the app looks for a family call started on another device. */
const ACTIVE_CALL_REFRESH_MS = 10_000;

export type CallStatus = 'connecting' | 'connected' | 'ended' | 'error';

export interface CallPeer extends CallParticipant {
  stream: MediaStream | null;
  hasVideo: boolean;
  connection: RTCPeerConnectionState;
}

export interface CallInfo {
  room: string;
  name: string;
  status: CallStatus;
  error: string | null;
  startedAt: number;
}

interface CallContextValue {
  call: CallInfo | null;
  peers: CallPeer[];
  localStream: MediaStream | null;
  activeCalls: ActiveCall[];
  muted: boolean;
  cameraOn: boolean;
  startCall: (room: string, name: string) => Promise<void>;
  endCall: () => void;
  toggleMute: () => void;
  toggleCamera: () => Promise<void>;
}

/**
 * The fallback keeps presentational tests simple. In the app every component
 * sits under CallProvider; a test that renders one panel on its own still gets
 * an inert call button instead of a missing-context crash.
 */
// eslint-disable-next-line react-refresh/only-export-components
export const callFallback: CallContextValue = {
  call: null,
  peers: [],
  localStream: null,
  activeCalls: [],
  muted: false,
  cameraOn: false,
  startCall: async () => {},
  endCall: () => {},
  toggleMute: () => {},
  toggleCamera: async () => {},
};

const CallContext = createContext<CallContextValue | null>(null);

// eslint-disable-next-line react-refresh/only-export-components
export function useCall(): CallContextValue {
  return useContext(CallContext) ?? callFallback;
}

function nativeServerUrl(): string {
  if (Capacitor.isNativePlatform()) {
    return storageGetSync('jarvis_server_url') || 'http://localhost';
  }
  return window.location.origin;
}

/** The signalling URL for one room. Exported so it can be unit-tested. */
// eslint-disable-next-line react-refresh/only-export-components
export function callSocketUrl(baseUrl: string, token: string, room: string, name: string): string {
  const url = new URL('/api/communication/calls/ws', baseUrl);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.searchParams.set('token', token);
  url.searchParams.set('room', room);
  url.searchParams.set('name', name);
  return url.toString();
}

function messageError(error: unknown): string {
  return error instanceof Error ? error.message : 'The call could not start.';
}

interface PeerConnection {
  id: string;
  username: string;
  name: string;
  muted: boolean;
  camera: boolean;
  polite: boolean;
  makingOffer: boolean;
  ignoreOffer: boolean;
  connection: RTCPeerConnectionState;
  stream: MediaStream | null;
  pc: RTCPeerConnection;
  pendingIce: RTCIceCandidateInit[];
}

interface CallEvents {
  onPeers: (peers: CallPeer[]) => void;
  onLocalStream: (stream: MediaStream | null) => void;
  onEnded: (message: string) => void;
}

function participant(value: unknown): CallParticipant | null {
  if (!value || typeof value !== 'object') return null;
  const raw = value as Record<string, unknown>;
  if (typeof raw.id !== 'string' || !raw.id) return null;
  return {
    id: raw.id,
    username: typeof raw.username === 'string' ? raw.username : '',
    name: typeof raw.name === 'string' ? raw.name : 'Family member',
    muted: Boolean(raw.muted),
    camera: Boolean(raw.camera),
  };
}

/**
 * One browser's side of a mesh call.
 *
 * The gateway is a signalling relay, never a media server. Each peer has one
 * RTCPeerConnection and the browser's perfect-negotiation pattern keeps the
 * two sides from deadlocking when both happen to offer at once.
 */
class CallSession {
  roomName: string;

  private readonly token: string;
  private readonly room: string;
  private readonly events: CallEvents;
  private ws: WebSocket | null = null;
  private selfId = '';
  private iceServers: RTCIceServer[] = [];
  private peers = new Map<string, PeerConnection>();
  private localStream: MediaStream | null = null;
  private muted = false;
  private camera = false;
  private closed = false;
  private pingTimer: number | null = null;

  constructor(token: string, room: string, name: string, events: CallEvents) {
    this.token = token;
    this.room = room;
    this.roomName = name || 'Family call';
    this.events = events;
  }

  async start(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('This device cannot make an audio call.');
    }
    try {
      this.localStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false,
      });
    } catch {
      throw new Error('Microphone permission is needed to join a family call.');
    }
    this.events.onLocalStream(this.localStream);

    const url = callSocketUrl(nativeServerUrl(), this.token, this.room, this.roomName);
    await this.connect(url);
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;

    if (this.pingTimer !== null) {
      window.clearInterval(this.pingTimer);
      this.pingTimer = null;
    }

    this.localStream?.getTracks().forEach((track) => track.stop());
    this.localStream = null;

    for (const peer of this.peers.values()) {
      peer.pc.onicecandidate = null;
      peer.pc.onnegotiationneeded = null;
      peer.pc.ontrack = null;
      peer.pc.onconnectionstatechange = null;
      peer.pc.close();
    }
    this.peers.clear();

    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      ws.onerror = null;
      ws.onmessage = null;
      ws.close();
    }

    this.events.onPeers([]);
    this.events.onLocalStream(null);
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    this.localStream?.getAudioTracks().forEach((track) => {
      track.enabled = !muted;
    });
    this.sendState();
  }

  async setCamera(enabled: boolean): Promise<void> {
    if (this.closed || this.camera === enabled) return;

    if (enabled) {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error('This device cannot use a camera.');
      }
      const video = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      const track = video.getVideoTracks()[0];
      if (!track) {
        video.getTracks().forEach((t) => t.stop());
        throw new Error('No camera was available.');
      }
      if (this.closed) {
        track.stop();
        return;
      }

      if (!this.localStream) this.localStream = new MediaStream();
      this.localStream.addTrack(track);
      for (const peer of this.peers.values()) {
        peer.pc.addTrack(track, this.localStream);
      }
      this.camera = true;
      this.events.onLocalStream(new MediaStream(this.localStream.getTracks()));
      this.sendState();
      return;
    }

    const videoTracks = this.localStream?.getVideoTracks() ?? [];
    for (const track of videoTracks) {
      for (const peer of this.peers.values()) {
        const sender = peer.pc.getSenders().find((candidate) => candidate.track === track);
        if (sender) peer.pc.removeTrack(sender);
      }
      this.localStream?.removeTrack(track);
      track.stop();
    }
    this.camera = false;
    this.events.onLocalStream(
      this.localStream ? new MediaStream(this.localStream.getTracks()) : null,
    );
    this.sendState();
  }

  private connect(url: string): Promise<void> {
    return new Promise((resolve, reject) => {
      let settled = false;
      const ws = new WebSocket(url);
      this.ws = ws;

      const timer = window.setTimeout(() => {
        if (settled) return;
        settled = true;
        reject(new Error('The call did not answer. Check that Jarvis is online.'));
        this.close();
      }, 15_000);

      const succeed = () => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        resolve();
      };
      const fail = (message: string) => {
        if (settled) {
          if (!this.closed) {
            this.events.onEnded(message);
            this.close();
          }
          return;
        }
        settled = true;
        window.clearTimeout(timer);
        // Stop the close handler from turning the server's explanation into a
        // generic "connection lost" before the provider can show it.
        ws.onclose = null;
        ws.onerror = null;
        ws.onmessage = null;
        try {
          ws.close();
        } catch {
          // The socket may already be closed.
        }
        reject(new Error(message));
      };

      ws.onmessage = (event) => {
        let message: Record<string, unknown>;
        try {
          message = JSON.parse(String(event.data)) as Record<string, unknown>;
        } catch {
          return;
        }
        const type = typeof message.type === 'string' ? message.type : '';

        if (type === 'welcome') {
          this.selfId = typeof (message.self as Record<string, unknown> | undefined)?.id === 'string'
            ? String((message.self as Record<string, unknown>).id)
            : '';
          if (typeof message.name === 'string' && message.name) this.roomName = message.name;
          this.iceServers = Array.isArray(message.ice_servers)
            ? (message.ice_servers as RTCIceServer[])
            : [];
          const peers = Array.isArray(message.peers) ? message.peers : [];
          for (const value of peers) {
            const info = participant(value);
            if (info) this.addPeer(info);
          }
          this.sendState();
          this.pingTimer = window.setInterval(() => this.send({ type: 'ping' }), 20_000);
          succeed();
          return;
        }

        if (type === 'error') {
          fail(typeof message.message === 'string' ? message.message : 'The call could not start.');
          return;
        }

        this.handleSignal(type, message);
      };

      ws.onerror = () => {
        fail('Could not reach the call service.');
      };

      ws.onclose = () => {
        if (!settled) {
          fail('The call service closed the connection.');
        } else if (!this.closed) {
          this.events.onEnded('The call ended because the connection was lost.');
          this.close();
        }
      };
    });
  }

  private handleSignal(type: string, message: Record<string, unknown>): void {
    const from = typeof message.from === 'string' ? message.from : '';
    if (!from) return;

    if (type === 'peer-joined') {
      const info = participant(message.peer);
      if (info) this.addPeer(info);
      return;
    }

    if (type === 'peer-left') {
      this.removePeer(from);
      return;
    }

    if (type === 'peer-state') {
      const info = participant(message.peer);
      const peer = this.peers.get(from);
      if (info && peer) {
        peer.muted = info.muted;
        peer.camera = info.camera;
        this.emitPeers();
      }
      return;
    }

    if (type === 'offer' && message.sdp) {
      void this.handleOffer(from, message.sdp as RTCSessionDescriptionInit);
      return;
    }

    if (type === 'answer' && message.sdp) {
      void this.handleAnswer(from, message.sdp as RTCSessionDescriptionInit);
      return;
    }

    if (type === 'ice' && message.candidate) {
      void this.handleIce(from, message.candidate as RTCIceCandidateInit);
    }
  }

  private addPeer(info: CallParticipant): void {
    const existing = this.peers.get(info.id);
    if (existing) {
      existing.username = info.username || existing.username;
      existing.name = info.name || existing.name;
      existing.muted = info.muted;
      existing.camera = info.camera;
      this.emitPeers();
      return;
    }

    const pc = new RTCPeerConnection({ iceServers: this.iceServers });
    const peer: PeerConnection = {
      id: info.id,
      username: info.username,
      name: info.name,
      muted: info.muted,
      camera: info.camera,
      // A deterministic side rolls back on glare; the other keeps its offer.
      polite: this.selfId.localeCompare(info.id) > 0,
      makingOffer: false,
      ignoreOffer: false,
      connection: pc.connectionState,
      stream: null,
      pc,
      pendingIce: [],
    };
    this.peers.set(info.id, peer);

    this.localStream?.getTracks().forEach((track) => {
      pc.addTrack(track, this.localStream as MediaStream);
    });

    pc.onicecandidate = (event) => {
      if (event.candidate) {
        this.send({
          type: 'ice',
          to: info.id,
          candidate: event.candidate.toJSON(),
        });
      }
    };

    pc.onnegotiationneeded = () => {
      void this.negotiate(peer);
    };

    pc.ontrack = (event) => {
      let stream = event.streams[0] ?? peer.stream;
      if (!stream) stream = new MediaStream();
      if (!stream.getTracks().some((track) => track.id === event.track.id)) {
        stream.addTrack(event.track);
      }
      peer.stream = stream;
      const update = () => this.emitPeers();
      event.track.addEventListener('mute', update);
      event.track.addEventListener('unmute', update);
      event.track.addEventListener('ended', update);
      this.emitPeers();
    };

    pc.onconnectionstatechange = () => {
      peer.connection = pc.connectionState;
      this.emitPeers();
    };

    this.emitPeers();
  }

  private removePeer(id: string): void {
    const peer = this.peers.get(id);
    if (!peer) return;
    peer.pc.close();
    this.peers.delete(id);
    this.emitPeers();
  }

  private async negotiate(peer: PeerConnection): Promise<void> {
    if (this.closed || peer.pc.signalingState === 'closed') return;
    try {
      peer.makingOffer = true;
      await peer.pc.setLocalDescription();
      if (peer.pc.localDescription) {
        this.send({ type: 'offer', to: peer.id, sdp: peer.pc.localDescription });
      }
    } catch (error) {
      console.warn('[call] could not create an offer:', error);
    } finally {
      peer.makingOffer = false;
    }
  }

  private async handleOffer(from: string, description: RTCSessionDescriptionInit): Promise<void> {
    let peer = this.peers.get(from);
    if (!peer) {
      this.addPeer({ id: from, username: '', name: 'Family member', muted: false, camera: false });
      peer = this.peers.get(from);
    }
    if (!peer) return;

    const collision = peer.makingOffer || peer.pc.signalingState !== 'stable';
    peer.ignoreOffer = !peer.polite && collision;
    if (peer.ignoreOffer) return;

    try {
      if (collision && peer.polite) {
        await peer.pc.setLocalDescription({ type: 'rollback' });
      }
      await peer.pc.setRemoteDescription(description);
      await this.flushIce(peer);
      await peer.pc.setLocalDescription();
      if (peer.pc.localDescription) {
        this.send({ type: 'answer', to: from, sdp: peer.pc.localDescription });
      }
    } catch (error) {
      console.warn('[call] could not answer:', error);
    }
  }

  private async handleAnswer(from: string, description: RTCSessionDescriptionInit): Promise<void> {
    const peer = this.peers.get(from);
    if (!peer || peer.pc.signalingState !== 'have-local-offer') return;
    try {
      await peer.pc.setRemoteDescription(description);
      await this.flushIce(peer);
    } catch (error) {
      console.warn('[call] could not use the answer:', error);
    }
  }

  private async handleIce(from: string, candidate: RTCIceCandidateInit): Promise<void> {
    const peer = this.peers.get(from);
    if (!peer) return;
    if (!peer.pc.remoteDescription) {
      peer.pendingIce.push(candidate);
      return;
    }
    try {
      await peer.pc.addIceCandidate(candidate);
    } catch (error) {
      if (!peer.ignoreOffer) console.warn('[call] could not add an ICE candidate:', error);
    }
  }

  private async flushIce(peer: PeerConnection): Promise<void> {
    const pending = peer.pendingIce.splice(0);
    for (const candidate of pending) {
      try {
        await peer.pc.addIceCandidate(candidate);
      } catch (error) {
        if (!peer.ignoreOffer) console.warn('[call] could not add a queued ICE candidate:', error);
      }
    }
  }

  private emitPeers(): void {
    this.events.onPeers(
      Array.from(this.peers.values()).map((peer) => ({
        id: peer.id,
        username: peer.username,
        name: peer.name,
        muted: peer.muted,
        camera: peer.camera,
        stream: peer.stream,
        hasVideo: Boolean(peer.stream?.getVideoTracks().some((track) => track.readyState === 'live' && !track.muted)),
        connection: peer.connection,
      })),
    );
  }

  private sendState(): void {
    this.send({ type: 'state', muted: this.muted, camera: this.camera });
  }

  private send(payload: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('') || '?';
}

function elapsed(startedAt: number, now: number): string {
  const total = Math.max(0, Math.floor((now - startedAt) / 1000));
  const seconds = total % 60;
  const minutes = Math.floor(total / 60) % 60;
  const hours = Math.floor(total / 3600);
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function CallVideo({ stream, muted = false, className }: { stream: MediaStream; muted?: boolean; className: string }) {
  const ref = useRef<HTMLVideoElement | null>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.srcObject = stream;
    void element.play().catch(() => {
      // Autoplay is best-effort; the user gesture that started the call
      // normally lets it play immediately.
    });
  }, [stream]);
  return <video ref={ref} autoPlay playsInline muted={muted} className={className} />;
}

function PeerTile({ peer }: { peer: CallPeer }) {
  return (
    <div
      className="relative aspect-square overflow-hidden rounded-2xl border border-white/10 bg-slate-900"
      data-testid={`call-peer-${peer.id}`}
    >
      {peer.stream && (
        <CallVideo
          stream={peer.stream}
          className={peer.hasVideo ? 'h-full w-full object-cover' : 'pointer-events-none absolute h-px w-px opacity-0'}
        />
      )}
      {!peer.hasVideo && (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 bg-gradient-to-br from-slate-800 to-slate-950">
          <span className="flex h-14 w-14 items-center justify-center rounded-full bg-white/10 text-lg font-bold text-white">
            {initials(peer.name)}
          </span>
          <span className="max-w-full truncate px-3 text-sm font-semibold text-slate-100">{peer.name}</span>
          <span className="text-[11px] text-slate-400">
            {peer.connection === 'connected' ? 'Audio call' : peer.connection === 'failed' ? 'Reconnecting…' : 'Connecting…'}
          </span>
        </div>
      )}
      {peer.hasVideo && peer.stream && (
        <span className="absolute bottom-0 left-0 right-0 truncate bg-gradient-to-t from-slate-950/90 to-transparent px-3 py-2 text-xs font-semibold text-white">
          {peer.name}
        </span>
      )}
      {peer.muted && (
        <span className="absolute right-2 top-2 rounded-full bg-slate-950/80 p-1.5 text-slate-200" title={`${peer.name} is muted`}>
          <MicOff size={13} />
        </span>
      )}
    </div>
  );
}

function CallOverlay({
  call,
  peers,
  localStream,
  muted,
  cameraOn,
  now,
  onMute,
  onCamera,
  onHangup,
}: {
  call: CallInfo;
  peers: CallPeer[];
  localStream: MediaStream | null;
  muted: boolean;
  cameraOn: boolean;
  now: number;
  onMute: () => void;
  onCamera: () => void;
  onHangup: () => void;
}) {
  const connecting = call.status === 'connecting';
  const finished = call.status === 'ended' || call.status === 'error';
  const talking = peers.some((peer) => peer.connection === 'connected');

  return (
    <div className="fixed inset-0 z-[120] flex items-end justify-center bg-slate-950/70 sm:items-end sm:justify-end sm:bg-transparent sm:p-5">
      <section
        className="flex h-full w-full flex-col overflow-hidden border-white/10 bg-slate-950 shadow-2xl sm:h-auto sm:max-h-[calc(100vh-2.5rem)] sm:w-[430px] sm:rounded-3xl sm:border"
        data-testid="call-overlay"
        role="dialog"
        aria-modal="true"
        aria-label={`${call.name || 'Family call'} call`}
      >
        <header className="flex items-center gap-3 border-b border-white/10 px-4 py-3">
          <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${finished ? 'bg-rose-500/15 text-rose-300' : 'bg-emerald-500/15 text-emerald-300'}`}>
            <Phone size={18} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate font-semibold text-white">{call.name || 'Family call'}</p>
            <p className="truncate text-xs text-slate-400">
              {call.status === 'connecting' && 'Connecting…'}
              {call.status === 'connected' && (
                `${peers.length + 1} in the call · ${elapsed(call.startedAt, now)}`
              )}
              {call.status === 'ended' && 'Call ended'}
              {call.status === 'error' && 'Call could not start'}
            </p>
          </div>
          <button
            type="button"
            onClick={onHangup}
            aria-label={finished ? 'Close the call panel' : 'Hang up'}
            className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/[0.07] text-slate-200 hover:bg-white/[0.12]"
          >
            <X size={18} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {finished ? (
            <div className="flex h-full min-h-48 flex-col items-center justify-center gap-3 text-center" data-testid="call-finished">
              <span className="flex h-14 w-14 items-center justify-center rounded-full bg-rose-500/15 text-rose-300">
                <PhoneOff size={24} />
              </span>
              <p className="text-sm leading-relaxed text-slate-300">{call.error || 'The call has ended.'}</p>
              <button
                type="button"
                onClick={onHangup}
                className="mt-1 min-h-10 rounded-xl bg-white/[0.08] px-4 text-sm font-semibold text-white hover:bg-white/[0.14]"
              >
                Close
              </button>
            </div>
          ) : connecting ? (
            <div className="flex h-full min-h-48 flex-col items-center justify-center gap-3 text-center" data-testid="call-connecting">
              <Loader2 size={26} className="animate-spin text-emerald-300" />
              <p className="text-sm text-slate-300">Connecting to the family call…</p>
              <p className="text-xs text-slate-500">Allow the microphone if your browser asks.</p>
            </div>
          ) : (
            <>
              {peers.length === 0 && (
                <p className="mb-3 rounded-xl bg-white/[0.05] px-3 py-2 text-center text-xs leading-relaxed text-slate-400" data-testid="call-waiting">
                  You are the first one here. The family can join from Family → Calls.
                </p>
              )}
              <div className="grid grid-cols-2 gap-2">
                {peers.map((peer) => <PeerTile key={peer.id} peer={peer} />)}
                <div className="relative aspect-square overflow-hidden rounded-2xl border border-emerald-400/30 bg-slate-900">
                  {cameraOn && localStream ? (
                    <CallVideo stream={localStream} muted className="h-full w-full object-cover" />
                  ) : (
                    <div className="flex h-full w-full flex-col items-center justify-center gap-2 bg-gradient-to-br from-slate-800 to-slate-950">
                      <span className="flex h-14 w-14 items-center justify-center rounded-full bg-white/10 text-lg font-bold text-white">
                        You
                      </span>
                      <span className="text-xs text-slate-400">{muted ? 'Microphone off' : 'Microphone on'}</span>
                    </div>
                  )}
                  <span className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-slate-950/90 to-transparent px-3 py-2 text-xs font-semibold text-white">
                    You
                  </span>
                </div>
              </div>
              {talking && (
                <p className="mt-3 text-center text-[11px] text-slate-500">
                  Audio runs directly between your devices. Nothing is recorded.
                </p>
              )}
            </>
          )}
        </div>

        {!finished && (
          <footer className="flex items-center justify-center gap-3 border-t border-white/10 px-4 py-4">
            <button
              type="button"
              onClick={onMute}
              disabled={connecting}
              aria-label={muted ? 'Unmute microphone' : 'Mute microphone'}
              aria-pressed={muted}
              className={`flex h-12 w-12 items-center justify-center rounded-full transition disabled:opacity-40 ${
                muted ? 'bg-rose-500 text-white' : 'bg-white/[0.08] text-slate-100 hover:bg-white/[0.14]'
              }`}
            >
              {muted ? <MicOff size={20} /> : <Mic size={20} />}
            </button>
            <button
              type="button"
              onClick={onCamera}
              disabled={connecting}
              aria-label={cameraOn ? 'Turn camera off' : 'Turn camera on'}
              aria-pressed={cameraOn}
              className={`flex h-12 w-12 items-center justify-center rounded-full transition disabled:opacity-40 ${
                cameraOn ? 'bg-indigo-500 text-white' : 'bg-white/[0.08] text-slate-100 hover:bg-white/[0.14]'
              }`}
            >
              {cameraOn ? <Video size={20} /> : <VideoOff size={20} />}
            </button>
            <button
              type="button"
              onClick={onHangup}
              aria-label="Hang up"
              className="flex h-12 w-14 items-center justify-center rounded-full bg-rose-500 text-white hover:bg-rose-400"
            >
              <PhoneOff size={20} />
            </button>
          </footer>
        )}
      </section>
    </div>
  );
}

/**
 * Owns the one call the app can be in. It sits above the router so moving
 * from Family to another page never drops the microphone or the call panel.
 */
export function CallProvider({ children }: { children: ReactNode }) {
  const { token } = useAuth();
  const tokenRef = useRef<string | null>(token);
  const sessionRef = useRef<CallSession | null>(null);
  const callRef = useRef<CallInfo | null>(null);
  const mutedRef = useRef(false);
  const cameraRef = useRef(false);

  const [call, setCall] = useState<CallInfo | null>(null);
  const [peers, setPeers] = useState<CallPeer[]>([]);
  const [localStream, setLocalStream] = useState<MediaStream | null>(null);
  const [activeCalls, setActiveCalls] = useState<ActiveCall[]>([]);
  const [muted, setMuted] = useState(false);
  const [cameraOn, setCameraOn] = useState(false);
  const [dismissed, setDismissed] = useState<string[]>([]);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    tokenRef.current = token;
  }, [token]);

  useEffect(() => {
    callRef.current = call;
  }, [call]);

  useEffect(() => {
    if (!call || call.status !== 'connected') return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [call]);

  useEffect(() => {
    if (!token || call) return;
    let cancelled = false;

    const load = async () => {
      try {
        const calls = await api.getActiveCalls();
        if (!cancelled) setActiveCalls(calls);
      } catch {
        // A call list is a convenience, not a reason to show an error on
        // every page when the gateway is briefly unavailable.
      }
    };
    void load();
    const timer = window.setInterval(load, ACTIVE_CALL_REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [token, call]);

  useEffect(() => {
    if (!call) {
      const tokens = new Set(activeCalls.map((active) => active.token));
      setDismissed((current) => current.filter((activeToken) => tokens.has(activeToken)));
    }
  }, [activeCalls, call]);

  useEffect(() => {
    return () => {
      sessionRef.current?.close();
      sessionRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (token) return;
    if (sessionRef.current) {
      sessionRef.current.close();
      sessionRef.current = null;
      setCall(null);
      setPeers([]);
      setLocalStream(null);
    }
  }, [token]);

  const startCall = useCallback(async (room: string, name: string) => {
    const apiToken = tokenRef.current;
    if (!apiToken) {
      setCall({
        room,
        name: name || 'Family call',
        status: 'error',
        error: 'Sign in to start a family call.',
        startedAt: Date.now(),
      });
      return;
    }

    if (sessionRef.current) {
      if (callRef.current?.room === room) return;
      toast.error('You are already in a call.');
      return;
    }

    setCall({
      room,
      name: name || 'Family call',
      status: 'connecting',
      error: null,
      startedAt: Date.now(),
    });
    setPeers([]);
    setLocalStream(null);
    setMuted(false);
    setCameraOn(false);
    mutedRef.current = false;
    cameraRef.current = false;

    const session = new CallSession(apiToken, room, name, {
      onPeers: setPeers,
      onLocalStream: setLocalStream,
      onEnded: (message) => {
        if (sessionRef.current === session) sessionRef.current = null;
        setPeers([]);
        setLocalStream(null);
        setCall((current) => current ? { ...current, status: 'ended', error: message } : current);
      },
    });
    sessionRef.current = session;

    try {
      await session.start();
      if (sessionRef.current !== session) return;
      setCall((current) => current
        ? { ...current, status: 'connected', name: session.roomName || current.name }
        : current);
    } catch (error) {
      if (sessionRef.current === session) sessionRef.current = null;
      session.close();
      setPeers([]);
      setLocalStream(null);
      setCall((current) => current && current.status === 'connecting'
        ? { ...current, status: 'error', error: messageError(error) }
        : current);
    }
  }, []);

  const endCall = useCallback(() => {
    const session = sessionRef.current;
    const room = callRef.current?.room;
    sessionRef.current = null;
    session?.close();
    if (room) setDismissed((current) => current.includes(room) ? current : [...current, room]);
    setCall(null);
    setPeers([]);
    setLocalStream(null);
    setMuted(false);
    setCameraOn(false);
    mutedRef.current = false;
    cameraRef.current = false;
  }, []);

  const toggleMute = useCallback(() => {
    const next = !mutedRef.current;
    mutedRef.current = next;
    setMuted(next);
    sessionRef.current?.setMuted(next);
  }, []);

  const toggleCamera = useCallback(async () => {
    const session = sessionRef.current;
    if (!session) return;
    const next = !cameraRef.current;
    try {
      await session.setCamera(next);
      cameraRef.current = next;
      setCameraOn(next);
    } catch (error) {
      toast.error(messageError(error));
    }
  }, []);

  const bannerCall = call ? undefined : activeCalls.find((active) => !dismissed.includes(active.token));

  const value = useMemo<CallContextValue>(() => ({
    call,
    peers,
    localStream,
    activeCalls,
    muted,
    cameraOn,
    startCall,
    endCall,
    toggleMute,
    toggleCamera,
  }), [call, peers, localStream, activeCalls, muted, cameraOn, startCall, endCall, toggleMute, toggleCamera]);

  return (
    <CallContext.Provider value={value}>
      {children}
      {bannerCall && (
        <div
          className="fixed bottom-4 left-1/2 z-[110] flex w-[min(92vw,26rem)] -translate-x-1/2 items-center gap-3 rounded-2xl border border-emerald-400/30 bg-slate-900 px-3 py-2.5 shadow-2xl"
          data-testid="call-banner"
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-300">
            <Phone size={17} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-white">{bannerCall.name || 'Family call'}</p>
            <p className="truncate text-[11px] text-slate-400">
              {bannerCall.participants.map((person) => person.name).join(', ') || 'Someone'} in the call
            </p>
          </div>
          <button
            type="button"
            onClick={() => void startCall(bannerCall.token, bannerCall.name || 'Family call')}
            className="min-h-9 shrink-0 rounded-xl bg-emerald-500 px-3 text-xs font-bold text-emerald-950 hover:bg-emerald-400"
          >
            Join
          </button>
          <button
            type="button"
            onClick={() => setDismissed((current) => [...current, bannerCall.token])}
            aria-label="Dismiss call alert"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-slate-400 hover:bg-white/[0.07] hover:text-white"
          >
            <X size={15} />
          </button>
        </div>
      )}
      {call && typeof document !== 'undefined' && createPortal(
        <CallOverlay
          call={call}
          peers={peers}
          localStream={localStream}
          muted={muted}
          cameraOn={cameraOn}
          now={now}
          onMute={toggleMute}
          onCamera={() => void toggleCamera()}
          onHangup={endCall}
        />,
        document.body,
      )}
    </CallContext.Provider>
  );
}
