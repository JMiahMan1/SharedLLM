import { getServerOrigin, getWsProtocolFor } from './serverUrl';
import type { MediaToken } from './mediaToken';
import { apiClient } from '../services/api';

/**
 * Real-time live video (go2rtc, through homehub and the gateway).
 *
 * The hub reports, per camera, whether go2rtc can play it (`realtime` in the
 * camera list) and which qualities it has. The page tries the modes in order,
 * best first, and falls back to the next when one fails:
 *
 *   webrtc  lowest latency; signaling through the gateway, media straight from
 *           go2rtc's WebRTC port (works on the home network)
 *   mse     fragmented MP4 over the gateway's WebSocket (works anywhere the
 *           app works, about a second behind)
 *   mp4     one long MP4 for a plain `<video>`, for a browser without MSE
 *   mjpeg   the existing picture stream (the caller's own fallback)
 */
export type LiveMode = 'webrtc' | 'mse' | 'mp4';
export type LiveQuality = 'sub' | 'main';

export interface CameraRealtime {
  modes: LiveMode[];
  qualities: LiveQuality[];
  default_quality: LiveQuality;
  on_demand?: boolean;
}

/** What this browser can play. */
export interface BrowserCaps {
  webrtc: boolean;
  mse: boolean;
}

export const MODE_ORDER: LiveMode[] = ['webrtc', 'mse', 'mp4'];

/** H.264 and H.265 profiles and the audio codecs go2rtc may send, as go2rtc's own player asks for them. */
export const MSE_CODECS = [
  'avc1.640029', 'avc1.64002A', 'avc1.640033', 'hvc1.1.6.L153.B0', 'mp4a.40.2', 'mp4a.40.5', 'flac', 'opus',
];

type MediaSourceCtor = { new (): MediaSource; isTypeSupported(type: string): boolean };

/** `MediaSource`, or Safari's `ManagedMediaSource` (iOS 17+), or null. */
export function mediaSourceCtor(win: Record<string, unknown> = window as unknown as Record<string, unknown>): MediaSourceCtor | null {
  const ctor = (win.ManagedMediaSource ?? win.MediaSource) as MediaSourceCtor | undefined;
  return ctor && typeof ctor.isTypeSupported === 'function' ? ctor : null;
}

export function browserCaps(win: Record<string, unknown> = window as unknown as Record<string, unknown>): BrowserCaps {
  return { webrtc: typeof win.RTCPeerConnection === 'function', mse: mediaSourceCtor(win) !== null };
}

/** The codecs to offer go2rtc, as the comma list its `mse` message expects. */
export function supportedCodecs(isSupported: (type: string) => boolean): string {
  return MSE_CODECS.filter((c) => {
    try {
      return isSupported(`video/mp4; codecs="${c}"`);
    } catch {
      return false;
    }
  }).join();
}

/** Modes that failed in this app session; a phone away from home should not wait on WebRTC every time. */
const failedModes = new Set<LiveMode>();

export function noteModeFailed(mode: LiveMode): void {
  failedModes.add(mode);
}

export function noteModeWorked(mode: LiveMode): void {
  failedModes.delete(mode);
}

export function resetModeMemory(): void {
  failedModes.clear();
}

/**
 * The modes to try for a camera, best first: what the hub says it can do,
 * what the browser can play, and modes that failed earlier this session last
 * (still tried, since the network may have changed). Empty when go2rtc cannot
 * play the camera: the caller shows the picture stream.
 */
export function playbackModes(realtime: CameraRealtime | null | undefined, caps: BrowserCaps): LiveMode[] {
  if (!realtime || !Array.isArray(realtime.modes) || realtime.qualities?.length === 0) return [];
  const usable = MODE_ORDER.filter((m) => realtime.modes.includes(m)
    && (m !== 'webrtc' || caps.webrtc) && (m !== 'mse' || caps.mse));
  return [...usable.filter((m) => !failedModes.has(m)), ...usable.filter((m) => failedModes.has(m))];
}

/** The mode after `current`, or null when the list is spent (show the picture stream). */
export function nextMode(modes: LiveMode[], current: LiveMode): LiveMode | null {
  const i = modes.indexOf(current);
  return i >= 0 && i + 1 < modes.length ? modes[i + 1] : null;
}

/** The quality to start with: one the camera has, the viewer's choice first. */
export function pickQuality(realtime: CameraRealtime, wanted?: LiveQuality | null): LiveQuality {
  if (wanted && realtime.qualities.includes(wanted)) return wanted;
  if (realtime.qualities.includes(realtime.default_quality)) return realtime.default_quality;
  return realtime.qualities[0] ?? 'sub';
}

/**
 * How long a mode may take to show its first frame before the next is tried.
 * A sleeping battery camera takes about 16 s to wake (measured), so it gets longer.
 */
export function firstFrameTimeoutMs(battery: boolean | undefined): number {
  return battery ? 35_000 : 15_000;
}

/** How long WebRTC may take to connect; behind a firewall it never does, and MSE should take over soon. */
export const WEBRTC_CONNECT_MS = 8_000;

function signed(token: MediaToken, quality: LiveQuality): string {
  return new URLSearchParams({ quality, user: token.user, mt: token.token }).toString();
}

function livePath(entityId: string, tail: string): string {
  return `/api/media/cameras/${encodeURIComponent(entityId)}/live/${tail}`;
}

/** Where to POST the WebRTC offer. */
export function webrtcUrl(token: MediaToken, entityId: string, quality: LiveQuality, origin = getServerOrigin()): string {
  return `${origin.replace(/\/+$/, '')}${livePath(entityId, 'webrtc')}?${signed(token, quality)}`;
}

/** The MSE WebSocket. */
export function mseSocketUrl(token: MediaToken, entityId: string, quality: LiveQuality, origin = getServerOrigin()): string {
  const base = origin.replace(/\/+$/, '');
  const host = base.replace(/^https?:\/\//, '');
  return `${getWsProtocolFor(base)}://${host}${livePath(entityId, 'ws')}?${signed(token, quality)}`;
}

/** The plain MP4 stream for a `<video src>`. */
export function mp4Url(token: MediaToken, entityId: string, quality: LiveQuality, origin = getServerOrigin()): string {
  return `${origin.replace(/\/+$/, '')}${livePath(entityId, 'stream.mp4')}?${signed(token, quality)}`;
}

// -- admin settings (the go2rtc integration) ---------------------------------------------------

export interface LiveVideoCamera {
  entity_id: string;
  name: string;
  platform: string;
  settings: { quality?: LiveQuality; on_demand?: boolean; disabled?: boolean; transcode?: boolean };
  qualities: LiveQuality[];
  registered: LiveQuality[];
  default_quality?: LiveQuality;
  battery?: boolean;
  on_demand?: boolean;
  note?: string | null;
}

export interface LiveVideoSettings {
  configured: boolean;
  entry_id: string | null;
  enabled: boolean;
  state: string | null;
  reason: string | null;
  url: string;
  username: string;
  has_password: boolean;
  options: { webrtc_host: string; webrtc_port: number; default_quality: LiveQuality };
  server: { version: string | null; error: string | null };
  cameras: LiveVideoCamera[];
}

export type LiveVideoChange = Partial<{
  url: string;
  username: string;
  password: string;
  enabled: boolean;
  webrtc_host: string;
  webrtc_port: number;
  default_quality: LiveQuality;
  cameras: Record<string, LiveVideoCamera['settings'] | null>;
}>;

const SETTINGS = '/api/admin/cameras/live-video';

export async function getLiveVideoSettings(): Promise<LiveVideoSettings> {
  return (await apiClient.get<LiveVideoSettings>(SETTINGS)).data;
}

export async function saveLiveVideoSettings(change: LiveVideoChange): Promise<LiveVideoSettings> {
  return (await apiClient.put<LiveVideoSettings>(SETTINGS, change)).data;
}

export async function syncLiveVideo(): Promise<LiveVideoSettings> {
  return (await apiClient.post<LiveVideoSettings>(`${SETTINGS}/sync`)).data;
}
