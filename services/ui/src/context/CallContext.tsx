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
import {
  AUTO_DROP_IN_EVENT,
  clearLegacyAutoDropIn,
  getDeviceDropIn,
  readLegacyAutoDropIn,
  storeDeviceDropIn,
} from '../lib/callSettings';
import { getDeviceKey } from '../lib/deviceRegistration';
import { listenForDropIn, nativeDropInStatus, reportDropInEnded, reportDropInJoined } from '../lib/dropIn';
import UserAvatar from '../components/UserAvatar';
import { useFamilyDirectory } from '../components/chat/familyDirectory';
import { setApkUpdateBusyCheck } from '../lib/appUpdater';

/** How often the app looks for a family drop-in call started elsewhere. */
const ACTIVE_CALL_REFRESH_MS = 5_000;
const RING_INTERVAL_MS = 2_500;
/** How long someone who joined another person's call stays once everyone else has left. */
const ALONE_HANGUP_MS = 15_000;
/** A drop-in answered from the background asks for the microphone this often, this many times (about 8 s). */
const DROP_IN_MIC_RETRY_MS = 800;
const DROP_IN_MIC_ATTEMPTS = 10;
// A drop-in call nobody answers used to chime every 2.5 seconds until somebody
// dismissed the banner, so a phone left on a table rang all afternoon. The
// banner stays up; the ringing stops after a minute.
const RING_MAX_MS = 60_000;

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
  /** You started this call and are ringing the others. */
  outgoing?: boolean;
  /** People who declined the ring. */
  declined?: string[];
  /** This phone joined by itself (Drop In); when it ends the app goes back to chat. */
  dropIn?: boolean;
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

/** Best-effort drop-in chime. Browsers may keep audio suspended until a tap. */
function playDropInChime(): void {
  try {
    navigator.vibrate?.([180, 80, 180]);
  } catch {
    // Vibration is unavailable on desktop and some browsers.
  }
  try {
    const AudioContextClass = window.AudioContext;
    if (!AudioContextClass) return;
    const context = new AudioContextClass();
    void context.resume().catch(() => {});
    const now = context.currentTime;
    const notes = [880, 1174.66, 880];
    notes.forEach((frequency, index) => {
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = 'sine';
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0.0001, now + index * 0.18);
      gain.gain.exponentialRampToValueAtTime(0.16, now + index * 0.18 + 0.03);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + index * 0.18 + 0.16);
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.start(now + index * 0.18);
      oscillator.stop(now + index * 0.18 + 0.18);
    });
    window.setTimeout(() => { void context.close().catch(() => {}); }, 1200);
  } catch {
    // Sound is a nicety; the alert still works without it.
  }
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
  onDeclined?: (name: string) => void;
}

/**
 * Who a signalling message is about: its "from", else the person it carries, else its "id". An older
 * server sent peer-joined and peer-state without "from", and dropping them left the newcomer nameless
 * ("Family member"), a leaver's tile up, and mute or camera changes unseen.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function signalSender(message: Record<string, unknown>): string {
  if (typeof message.from === 'string' && message.from) return message.from;
  const carried = message.peer as Record<string, unknown> | undefined;
  if (typeof carried?.id === 'string' && carried.id) return carried.id;
  return typeof message.id === 'string' ? message.id : '';
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
  /** How many people were already in the call when this browser joined. */
  peersAtJoin = 0;

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

  /**
   * ``micAttempts`` > 1 for a drop-in: the phone answers it from the background, and Android lends the
   * microphone only once the drop-in's foreground service is up, a moment after the app is told to join.
   * Asking again shortly instead of giving up is what keeps a drop-in from ever needing a tap.
   */
  async start(micAttempts = 1): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('This device cannot make an audio call.');
    }
    for (let attempt = 1; ; attempt++) {
      try {
        this.localStream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
          video: false,
        });
        break;
      } catch (error) {
        if (this.closed) throw new Error('The call ended.', { cause: error });
        if (attempt >= micAttempts) {
          console.warn('[calls] microphone refused', error);
          throw new Error('Microphone permission is needed to join a family call.', { cause: error });
        }
        await new Promise((resolve) => window.setTimeout(resolve, DROP_IN_MIC_RETRY_MS));
      }
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
          this.peersAtJoin = peers.length;
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
    if (type === 'declined') {
      this.events.onDeclined?.(typeof message.name === 'string' && message.name ? message.name : 'Someone');
      return;
    }
    const from = signalSender(message);
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
  // The person's picture and name from the family directory (the one chat uses); the call's own name when
  // the directory has none.
  const person = useFamilyDirectory()(peer.username || peer.name);
  const name = person?.display_name || peer.name;
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
          <UserAvatar username={person?.username || peer.username || name} avatar={person?.avatar} name={name} size={56} />
          <span className="max-w-full break-words px-3 text-center text-sm font-semibold text-slate-100">{name}</span>
          <span className="text-[11px] text-slate-400">
            {peer.connection === 'connected' ? 'Audio call' : peer.connection === 'failed' ? 'Reconnecting…' : 'Connecting…'}
          </span>
        </div>
      )}
      {peer.hasVideo && peer.stream && (
        <span className="absolute bottom-0 left-0 right-0 truncate bg-gradient-to-t from-slate-950/90 to-transparent px-3 py-2 text-xs font-semibold text-white">
          {name}
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
  // Calling and nobody has picked up yet.
  const calling = call.status === 'connected' && call.outgoing && peers.length === 0;
  const unanswered = calling && now - call.startedAt >= RING_MAX_MS;

  return (
    <div className="fixed inset-0 z-[120] flex h-[100dvh] w-full items-stretch justify-center bg-slate-950/70 sm:pointer-events-none sm:h-auto sm:items-end sm:justify-end sm:bg-transparent sm:p-5">
      <section
        className="flex h-full max-h-[100dvh] w-full flex-col overflow-hidden border-white/10 bg-slate-950 shadow-2xl sm:pointer-events-auto sm:h-auto sm:max-h-[calc(100vh-2.5rem)] sm:w-[430px] sm:rounded-3xl sm:border"
        data-testid="call-overlay"
        role="dialog"
        aria-modal="true"
        aria-label={`${call.name || 'Family call'} call`}
      >
        <header
          className="flex items-center gap-3 border-b border-white/10 px-4 pb-3"
          style={{ paddingTop: 'max(0.75rem, env(safe-area-inset-top))' }}
        >
          <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${finished ? 'bg-rose-500/15 text-rose-300' : 'bg-emerald-500/15 text-emerald-300'}`}>
            <Phone size={18} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate font-semibold text-white">{call.name || 'Family call'}</p>
            <p className="truncate text-xs text-slate-400">
              {call.status === 'connecting' && 'Connecting…'}
              {calling && (unanswered ? 'No answer yet' : 'Calling…')}
              {call.status === 'connected' && !calling && (
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
              <p className="text-sm text-slate-300">Connecting…</p>
              <p className="text-xs text-slate-500">Allow the microphone if your browser asks.</p>
            </div>
          ) : (
            <>
              {calling ? (
                <div className="mb-3 flex flex-col items-center gap-1 rounded-xl bg-emerald-500/[0.07] px-3 py-3 text-center" data-testid="call-ringing">
                  <span className="flex items-center gap-2 text-sm font-semibold text-emerald-200">
                    {!unanswered && <span className="h-2 w-2 animate-ping rounded-full bg-emerald-400" />}
                    {unanswered ? 'No one has answered yet' : `Calling ${call.name || 'the family'}…`}
                  </span>
                  <span className="text-xs leading-relaxed text-slate-400">
                    {unanswered
                      ? 'You can keep waiting - they can still join from Family → Calls - or hang up.'
                      : 'Phones with auto drop-in answer by themselves; everyone else gets a ringing call.'}
                  </span>
                </div>
              ) : peers.length === 0 && (
                <p className="mb-3 rounded-xl bg-white/[0.05] px-3 py-2 text-center text-xs leading-relaxed text-slate-400" data-testid="call-waiting">
                  Waiting for others to join.
                </p>
              )}
              {(call.declined?.length ?? 0) > 0 && peers.length === 0 && (
                <p className="mb-3 text-center text-xs text-rose-300" data-testid="call-declined">
                  {call.declined!.join(', ')} declined.
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
          <footer
            className="flex items-center justify-center gap-3 border-t border-white/10 px-4 pt-4"
            style={{ paddingBottom: 'max(1rem, env(safe-area-inset-bottom))' }}
          >
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

/** Full-screen incoming call: answer or decline, like a phone call. */
function IncomingCall({ call, onAnswer, onDecline }: { call: ActiveCall; onAnswer: () => void; onDecline: () => void }) {
  const caller = call.caller || call.participants[0]?.name || 'Someone';
  const person = useFamilyDirectory()(call.participants[0]?.username || caller);
  const group = call.name && call.name !== caller ? call.name : '';
  return (
    <div
      className="fixed inset-0 z-[140] flex flex-col items-center justify-between bg-gradient-to-b from-emerald-950 via-slate-950 to-slate-950 px-6 text-center sm:inset-auto sm:bottom-5 sm:right-5 sm:w-[360px] sm:rounded-3xl sm:border sm:border-emerald-400/30 sm:py-6 sm:shadow-2xl"
      style={{ paddingTop: 'max(3.5rem, env(safe-area-inset-top))', paddingBottom: 'max(2.5rem, env(safe-area-inset-bottom))' }}
      role="alertdialog"
      aria-modal="true"
      aria-label={`${caller} is calling`}
      data-testid="incoming-call"
    >
      <div className="flex flex-col items-center gap-3 sm:gap-2">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-emerald-300">Family call</p>
        <span className="relative mt-4 flex h-28 w-28 items-center justify-center sm:mt-2 sm:h-20 sm:w-20">
          <span className="absolute inset-0 animate-ping rounded-full bg-emerald-400/20" />
          <span className="relative flex h-full w-full items-center justify-center">
            <UserAvatar username={person?.username || caller} avatar={person?.avatar} name={caller}
              size={112} className="h-full w-full" />
          </span>
        </span>
        <p className="mt-2 text-3xl font-bold tracking-tight text-white sm:text-xl">{caller}</p>
        <p className="text-sm text-slate-300">{group ? `is calling ${group}` : 'is calling you'}</p>
      </div>
      <div className="flex w-full max-w-xs items-center justify-around sm:mt-6">
        <button
          type="button"
          onClick={onDecline}
          aria-label="Decline"
          className="flex flex-col items-center gap-2 text-xs font-semibold text-slate-300"
        >
          <span className="flex h-16 w-16 items-center justify-center rounded-full bg-rose-500 text-white shadow-lg transition active:scale-95 hover:bg-rose-400">
            <PhoneOff size={26} />
          </span>
          Decline
        </button>
        <button
          type="button"
          onClick={onAnswer}
          aria-label="Answer"
          className="flex flex-col items-center gap-2 text-xs font-semibold text-slate-300"
        >
          <span className="flex h-16 w-16 animate-bounce items-center justify-center rounded-full bg-emerald-500 text-white shadow-lg transition active:scale-95 hover:bg-emerald-400">
            <Phone size={26} />
          </span>
          Answer
        </button>
      </div>
    </div>
  );
}

/**
 * Owns the one call the app can be in. It sits above the router so moving
 * from Family to another page never drops the microphone or the call panel.
 *
 * Calling someone rings the conversation's other members (the gateway sends
 * their phones a notice too). Each of their devices answers the way its
 * owner chose: with auto drop-in on it joins by itself after a chime, like an
 * Alexa drop-in; otherwise it shows a full-screen incoming call to answer or
 * decline. A call that is no longer ringing (a group call in progress) shows
 * as a quiet Join banner.
 */
export function CallProvider({ children }: { children: ReactNode }) {
  const { token, user } = useAuth();
  const tokenRef = useRef<string | null>(token);
  const usernameRef = useRef<string | undefined>(user?.username);
  const sessionRef = useRef<CallSession | null>(null);
  const callRef = useRef<CallInfo | null>(null);
  const mutedRef = useRef(false);
  const cameraRef = useRef(false);
  const autoDropInRef = useRef(false);
  const autoTriedRef = useRef<Set<string>>(new Set());
  const autoJoinRef = useRef<(active: ActiveCall) => void>(() => {});

  const [call, setCall] = useState<CallInfo | null>(null);
  const [peers, setPeers] = useState<CallPeer[]>([]);
  const [localStream, setLocalStream] = useState<MediaStream | null>(null);
  const [activeCalls, setActiveCalls] = useState<ActiveCall[]>([]);
  const [muted, setMuted] = useState(false);
  const [cameraOn, setCameraOn] = useState(false);
  const [dismissed, setDismissed] = useState<string[]>([]);
  const [autoDropIn, setAutoDropIn] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  // An automatic app update restarts the app: never in the middle of a call.
  useEffect(() => {
    setApkUpdateBusyCheck(() => callRef.current !== null);
    return () => setApkUpdateBusyCheck(() => false);
  }, []);

  useEffect(() => {
    tokenRef.current = token;
    usernameRef.current = user?.username;
    if (!token) {
      autoTriedRef.current = new Set();
      setActiveCalls([]);
      setDismissed([]);
    }
  }, [token, user?.username]);

  useEffect(() => {
    let active = true;
    const refresh = () => {
      void getDeviceDropIn().then((enabled) => {
        if (!active) return;
        autoDropInRef.current = enabled;
        setAutoDropIn(enabled);
      });
    };
    refresh();
    window.addEventListener(AUTO_DROP_IN_EVENT, refresh);
    window.addEventListener('storage', refresh);
    return () => {
      active = false;
      window.removeEventListener(AUTO_DROP_IN_EVENT, refresh);
      window.removeEventListener('storage', refresh);
    };
  }, []);

  // Before drop-in was per person, a device's switch let anyone in the
  // conversation drop in. Carry an "on" over once: the person's list becomes
  // everyone in the family (they can narrow it in Settings).
  useEffect(() => {
    if (!token || !user?.username) return;
    let cancelled = false;
    void (async () => {
      try {
        const legacy = await readLegacyAutoDropIn();
        if (legacy === null) return;
        if (legacy === '1') {
          const settings = await api.getDropInSettings();
          const mine = settings.settings.find((s) => s.username === settings.me);
          if (cancelled) return;
          if (mine?.can_edit && !mine.configured) {
            await api.setDropInList(
              settings.me,
              settings.people.map((p) => p.username).filter((u) => u !== settings.me),
            );
          }
        }
        await clearLegacyAutoDropIn();
      } catch {
        // Try again next time the app opens.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, user?.username]);

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
        const deviceKey = await getDeviceKey().catch(() => '');
        const { calls, deviceDropIn } = await api.getCallList(deviceKey || undefined);
        if (cancelled) return;
        // A malformed answer must not take the banner down with it.
        const list = Array.isArray(calls) ? calls : [];
        setActiveCalls(list);
        if (deviceDropIn !== undefined && deviceDropIn !== autoDropInRef.current) {
          autoDropInRef.current = deviceDropIn;
          setAutoDropIn(deviceDropIn);
          void storeDeviceDropIn(deviceDropIn);
        }

        // Drop-in: a call from someone this person lets drop in is answered
        // by this device on its own - also right after the app opens (from
        // the call notice). Any other ring rings.
        if (!callRef.current && autoDropInRef.current) {
          const username = usernameRef.current;
          const ringing = list.find((active) =>
            active.ringing
            && active.drop_in
            && !autoTriedRef.current.has(active.token)
            && !(username && active.participants.some((person) => person.username === username)));
          if (ringing) {
            autoTriedRef.current.add(ringing.token);
            playDropInChime();
            autoJoinRef.current(ringing);
          }
        }
        const tokens = new Set(list.map((active) => active.token));
        for (const tried of autoTriedRef.current) {
          if (!tokens.has(tried)) autoTriedRef.current.delete(tried);
        }
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

  const startCall = useCallback(async (room: string, name: string, options?: { auto?: boolean }) => {
    const apiToken = tokenRef.current;
    if (!apiToken) {
      if (!options?.auto) {
        setCall({
          room,
          name: name || 'Family call',
          status: 'error',
          error: 'Sign in to start a family call.',
          startedAt: Date.now(),
        });
      }
      return;
    }

    if (sessionRef.current) {
      if (callRef.current?.room === room) return;
      if (!options?.auto) toast.error('You are already in a call.');
      return;
    }

    setCall({
      room,
      name: name || 'Family call',
      status: 'connecting',
      error: null,
      startedAt: Date.now(),
      ...(options?.auto ? { dropIn: true } : {}),
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
      onDeclined: (who) => {
        if (sessionRef.current !== session) return;
        setCall((current) => current
          ? { ...current, declined: [...(current.declined ?? []).filter((n) => n !== who), who] }
          : current);
      },
    });
    sessionRef.current = session;

    try {
      await session.start(options?.auto ? DROP_IN_MIC_ATTEMPTS : 1);
      if (sessionRef.current !== session) return;
      reportDropInJoined(room);  // the phone's safety net need not ring it
      if (options?.auto && session.peersAtJoin === 0) {
        // A Drop In whose caller has already gone: never sit in an empty room with the mic on.
        sessionRef.current = null;
        session.close();
        setPeers([]);
        setLocalStream(null);
        setCall(null);
        return;
      }
      // Nobody was there and this person started it: a new call, so ring the others. A phone that answered
      // a Drop In by itself never rings anyone.
      const outgoing = !options?.auto && session.peersAtJoin === 0;
      setCall((current) => current
        ? { ...current, status: 'connected', name: session.roomName || current.name, outgoing, startedAt: Date.now() }
        : current);
      if (outgoing) {
        void api.ringCall(room).then(({ ringing, reason }) => {
          // A ring that reached nobody must not look like one nobody answered.
          if (ringing.length === 0 && reason && sessionRef.current === session) {
            toast.error(`Nobody was rung: ${reason}`);
          }
        }).catch(() => {
          // The call still works; the others can see it in their call list.
        });
      }
    } catch (error) {
      if (sessionRef.current === session) sessionRef.current = null;
      session.close();
      setPeers([]);
      setLocalStream(null);
      if (options?.auto) {
        setCall(null);
        toast.error('Could not drop in automatically. Tap the call banner to join.');
      } else {
        setCall((current) => current && current.status === 'connecting'
          ? { ...current, status: 'error', error: messageError(error) }
          : current);
      }
    }
  }, []);

  useEffect(() => {
    autoJoinRef.current = (active) => {
      void startCall(active.token, active.name || 'Family call', { auto: true });
    };
  }, [startCall]);

  // Android answered a ring by itself while the app was in the background or
  // the phone was locked (DropInCall.java): join the room now.
  const endCallRef = useRef<() => void>(() => {});
  useEffect(() => listenForDropIn(
    (room, name) => {
      autoTriedRef.current.add(room);
      // The one sound a drop-in makes. Newer APKs ding by themselves as they answer; older ones do not.
      void nativeDropInStatus().then((status) => { if (!status) playDropInChime(); });
      void startCall(room, name, { auto: true }).then(() => {
        // Signed out, or already in another call: nothing joined, so the
        // native call must not stay up holding the microphone.
        if (callRef.current?.room !== room) reportDropInEnded();
      });
    },
    () => endCallRef.current(),
  ), [startCall]);

  // Whenever this device is not in a live call, the native drop-in call (if
  // any) must not linger: its notification and microphone end with ours.
  const hadCallRef = useRef(false);
  useEffect(() => {
    const live = Boolean(call && (call.status === 'connecting' || call.status === 'connected'));
    if (live) {
      hadCallRef.current = true;
    } else if (hadCallRef.current) {
      hadCallRef.current = false;
      reportDropInEnded();
    }
  }, [call]);

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

  useEffect(() => {
    endCallRef.current = endCall;
  }, [endCall]);

  // Someone who joined another person's call hangs up once they have been alone in it for a while: the
  // others left, so it is over (a phone left in an empty room kept its microphone open for ten minutes).
  // The person who started the call keeps waiting, as they choose.
  const alone = Boolean(call && call.status === 'connected' && !call.outgoing && peers.length === 0);
  useEffect(() => {
    if (!alone) return undefined;
    const id = window.setTimeout(() => endCallRef.current(), ALONE_HANGUP_MS);
    return () => window.clearTimeout(id);
  }, [alone]);

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

  const decline = useCallback((room: string) => {
    setDismissed((current) => current.includes(room) ? current : [...current, room]);
    void api.declineCall(room).catch(() => {
      // The ring stops on its own after a minute.
    });
  }, []);

  const visible = call ? [] : activeCalls.filter((active) => !dismissed.includes(active.token));
  // With auto drop-in on, the device is about to answer by itself.
  const incomingCall = visible.find((active) => active.ringing && !(autoDropIn && active.drop_in));
  const bannerCall = incomingCall ? undefined : visible.find((active) => !active.ringing);
  const incomingToken = incomingCall?.token;
  const bannerToken = bannerCall?.token;

  // An incoming call rings until it is answered, declined or stops ringing
  // (the gateway rings for a minute) - and never longer than RING_MAX_MS, so
  // a phone left on a table cannot ring all afternoon.
  useEffect(() => {
    if (!incomingToken) return;
    playDropInChime();
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      if (Date.now() - startedAt >= RING_MAX_MS) {
        window.clearInterval(timer);
        return;
      }
      playDropInChime();
    }, RING_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [incomingToken]);

  // A call already in progress is not ringing anybody: one chime when it
  // first shows, then a quiet banner.
  useEffect(() => {
    if (bannerToken) playDropInChime();
  }, [bannerToken]);

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
      {incomingCall && typeof document !== 'undefined' && createPortal(
        <IncomingCall
          call={incomingCall}
          onAnswer={() => void startCall(incomingCall.token, incomingCall.name || 'Family call')}
          onDecline={() => decline(incomingCall.token)}
        />,
        document.body,
      )}
      {bannerCall && typeof document !== 'undefined' && createPortal(
        <div
          className="fixed bottom-[calc(var(--bottom-bar)+0.75rem)] left-1/2 z-[130] flex w-[min(92vw,28rem)] -translate-x-1/2 items-center gap-3 rounded-2xl border border-emerald-400/30 bg-slate-900 px-3 py-2.5 shadow-2xl"
          data-testid="call-banner"
        >
          <span className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-300">
            <Phone size={17} />
            <span className="absolute -right-0.5 -top-0.5 flex h-3 w-3">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex h-3 w-3 rounded-full border-2 border-slate-900 bg-emerald-400" />
            </span>
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-white">{bannerCall.name || 'Family call'}</p>
            <p className="truncate text-[11px] text-emerald-300">
              {bannerCall.participants.map((person) => person.name).join(', ') || 'Someone'} on a call
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
        </div>,
        document.body,
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
