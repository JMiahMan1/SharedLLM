/**
 * The private chat's video clips on the device: the limits, the recorder's
 * format, and reading a clip's length, frame size and poster frame before it
 * is encrypted (lib/privateChatCrypto.ts). Nothing here writes to disk: the
 * clip is read from the picked file or the recorder's memory, and the poster
 * is drawn to a canvas and turned into JPEG bytes in memory.
 */
import { MAX_VIDEO_BYTES, VIDEO_TYPES } from './privateChatCrypto';

/** The longest clip the in-app recorder makes: it stops by itself at three minutes. */
export const RECORD_LIMIT_S = 180;
/**
 * What the recorder asks for. At these rates three minutes is about 38 MB,
 * inside MAX_VIDEO_BYTES with room for the encoder overshooting.
 */
export const RECORDER_VIDEO_BPS = 1_600_000;
export const RECORDER_AUDIO_BPS = 96_000;
/** The recorder also stops this far short of the byte limit, whatever the clock says. */
export const RECORD_BYTE_MARGIN = 2 * 1024 * 1024;
/** The poster frame's longest side, in pixels. */
export const POSTER_MAX_SIDE = 640;

/**
 * The recorder's container, best first: MP4 (H.264/AAC) plays on every phone,
 * WebM where MP4 recording is not offered (older Chrome, Firefox). Empty when
 * the browser offers none of them.
 */
export const RECORDER_TYPES = [
  'video/mp4;codecs=avc1.42E01E,mp4a.40.2',
  'video/mp4',
  'video/webm;codecs=vp9,opus',
  'video/webm;codecs=vp8,opus',
  'video/webm',
] as const;

/**
 * The first container the recorder can make and this browser can also play
 * back (a build without H.264 may offer MP4 recording it cannot show in the
 * preview). `canPlay` is the video element's canPlayType.
 */
export function recorderMimeType(
  isTypeSupported: ((type: string) => boolean) | undefined,
  canPlay: (type: string) => string = () => 'maybe',
): string {
  if (!isTypeSupported) return '';
  return RECORDER_TYPES.find((type) => {
    try {
      return isTypeSupported(type) && canPlay(type) !== '';
    } catch {
      return false;
    }
  }) ?? '';
}

/** "video/webm;codecs=vp8,opus" is played as "video/webm". */
export function baseMime(type: string): string {
  return type.split(';')[0].trim().toLowerCase();
}

const BY_EXTENSION: Record<string, string> = {
  mp4: 'video/mp4',
  m4v: 'video/mp4',
  webm: 'video/webm',
  mov: 'video/quicktime',
  '3gp': 'video/3gpp',
};

/** A picked file's video type: what the phone said, or from its name when it said nothing. Empty when not a clip we play. */
export function videoMimeOf(file: { type?: string; name?: string }): string {
  const said = baseMime(file.type || '');
  if (said) return VIDEO_TYPES.has(said) ? said : '';
  const ext = /\.([a-z0-9]+)$/i.exec(file.name || '')?.[1]?.toLowerCase() ?? '';
  return BY_EXTENSION[ext] ?? '';
}

export function isVideoFile(file: { type?: string; name?: string }): boolean {
  return Boolean(videoMimeOf(file)) || baseMime(file.type || '').startsWith('video/');
}

/** Why a picked clip cannot be sent, or null when it can. */
export function videoProblem(file: { type?: string; name?: string; size: number }): string | null {
  if (!videoMimeOf(file)) return 'That kind of video cannot be sent here (MP4, WebM, MOV or 3GP).';
  if (!file.size) return 'That video is empty.';
  if (file.size > MAX_VIDEO_BYTES) return `Videos are limited to ${Math.round(MAX_VIDEO_BYTES / (1024 * 1024))} MB. Trim it on the phone first.`;
  return null;
}

/** Whether the recorder must stop now: the clock reached the limit, or the clip nearly fills the byte limit. */
export function recorderMustStop(elapsedS: number, bytes: number): 'time' | 'size' | null {
  if (elapsedS >= RECORD_LIMIT_S) return 'time';
  if (bytes >= MAX_VIDEO_BYTES - RECORD_BYTE_MARGIN) return 'size';
  return null;
}

/** 7 → "0:07", 179 → "2:59", 3723 → "1:02:03". */
export function formatDuration(seconds: number): string {
  const total = Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds) : 0;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
}

/** The poster's size: the frame scaled so its longest side is at most POSTER_MAX_SIDE. */
export function posterSize(width: number, height: number, max = POSTER_MAX_SIDE): { width: number; height: number } {
  if (!width || !height) return { width: max, height: Math.round((max * 9) / 16) };
  const scale = Math.min(1, max / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

export interface ClipFacts {
  /** Seconds; 0 when the browser could not tell. */
  duration: number;
  width: number;
  height: number;
  /** A JPEG of a frame near the start, in memory. The caller encrypts and zeroes it. */
  poster: Uint8Array<ArrayBuffer>;
}

function once(target: EventTarget, events: string[], timeoutMs: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const done = (name: string) => () => {
      for (const e of events) target.removeEventListener(e, handlers[e]);
      window.clearTimeout(timer);
      if (name === 'error') reject(new Error('This video could not be read on this device.'));
      else resolve(name);
    };
    const handlers: Record<string, () => void> = {};
    for (const e of events) {
      handlers[e] = done(e);
      target.addEventListener(e, handlers[e]);
    }
    const timer = window.setTimeout(() => {
      for (const e of events) target.removeEventListener(e, handlers[e]);
      reject(new Error('This video could not be read on this device.'));
    }, timeoutMs);
  });
}

/**
 * Read a clip's length and frame size and draw its poster, all on the device.
 * `knownDuration` is the recorder's own clock: a recording from MediaRecorder
 * often says nothing about its length until it has been played through.
 */
export async function readClip(source: Blob, knownDuration?: number): Promise<ClipFacts> {
  const video = document.createElement('video');
  video.muted = true;
  video.playsInline = true;
  video.preload = 'auto';
  const url = URL.createObjectURL(source);
  const canvas = document.createElement('canvas');
  try {
    video.src = url;
    await once(video, ['loadeddata', 'error'], 15_000);
    let duration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 0;
    if (!duration && !knownDuration) {
      // A clip whose length is not in its header: seeking far past the end makes the browser find it.
      video.currentTime = 1e7;
      await once(video, ['seeked', 'durationchange', 'error'], 5_000).catch(() => '');
      duration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 0;
    }
    if (knownDuration && knownDuration > 0) duration = knownDuration;
    // A frame a little way in (the first is often black), never past the end.
    const at = duration ? Math.min(1, duration / 4) : 0;
    if (Math.abs(video.currentTime - at) > 0.01) {
      video.currentTime = at;
      await once(video, ['seeked', 'error'], 5_000).catch(() => '');
    }
    const width = video.videoWidth;
    const height = video.videoHeight;
    const size = posterSize(width, height);
    canvas.width = size.width;
    canvas.height = size.height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('This device cannot draw the video.');
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(0, 0, size.width, size.height);
    if (width && height) ctx.drawImage(video, 0, 0, size.width, size.height);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.8));
    if (!blob) throw new Error('Could not make the picture for the video.');
    return { duration, width, height, poster: new Uint8Array(await blob.arrayBuffer()) };
  } finally {
    video.removeAttribute('src');
    video.load();
    URL.revokeObjectURL(url);
    canvas.width = 0;
    canvas.height = 0;
  }
}
