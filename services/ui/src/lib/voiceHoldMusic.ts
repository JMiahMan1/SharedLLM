import { apiClient } from '../services/api';

/**
 * Devices > Setup > Voice hold music: what an Echo plays while Jarvis works on a long answer, and the
 * playful lines said before it (services/homehub/hold_music.py through
 * services/gateway/voice_hold_music_routes.py, admins only). An uploaded clip goes to the hub, is decoded
 * there once and is never served back.
 */

export type HoldMusicSource = 'gameshow' | 'builtin' | 'upload';

export interface HoldMusicUpload {
  name: string;
  type: string;
  seconds: number;
  bytes: number;
  uploaded_at: number;
  /** The setting names a clip whose file is gone: the built-in tune plays. */
  missing?: boolean;
}

export interface HoldMusicLimits {
  types: string[];
  max_bytes: number;
  max_seconds: number;
  max_lines: number;
  max_line_chars: number;
}

export interface HoldMusicSettings {
  source: HoldMusicSource;
  sources: Array<{ id: HoldMusicSource; label: string }>;
  /** The lines said before the music; empty means the hub's defaults. */
  lines: string[];
  upload: HoldMusicUpload | null;
  limits: HoldMusicLimits;
}

const BASE = '/api/homehub/voice/hold-music';

export async function getHoldMusic(): Promise<HoldMusicSettings> {
  return (await apiClient.get(BASE)).data;
}

export async function setHoldMusic(body: { source?: HoldMusicSource; lines?: string[] }): Promise<HoldMusicSettings> {
  return (await apiClient.put(BASE, body)).data;
}

/** Send a clip; the hub decodes and checks it (type, size, length) and makes it the hold music. */
export async function uploadHoldMusic(file: File): Promise<HoldMusicSettings> {
  const form = new FormData();
  form.append('file', file, file.name);
  return (await apiClient.post(`${BASE}/clip`, form, {
    headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120_000,
  })).data;
}

export async function deleteHoldMusicClip(): Promise<HoldMusicSettings> {
  return (await apiClient.delete(`${BASE}/clip`)).data;
}

/** The checks the hub makes that can be made before sending: the type by name, and the size. */
export function clipProblem(file: Pick<File, 'name' | 'size'>, limits: HoldMusicLimits): string | null {
  const ext = file.name.includes('.') ? file.name.split('.').pop()!.toLowerCase() : '';
  if (!limits.types.includes(ext)) {
    return `Choose a ${limits.types.slice(0, -1).join(', ')} or ${limits.types.at(-1)} file`;
  }
  if (file.size > limits.max_bytes) {
    return `That file is ${(file.size / 1048576).toFixed(1)} MB; the most is ${Math.floor(limits.max_bytes / 1048576)} MB`;
  }
  if (file.size === 0) return 'That file is empty';
  return null;
}

/** One line per row of the box, blank rows left out. */
export function linesFromText(text: string): string[] {
  return text.split('\n').map((line) => line.replace(/\s+/g, ' ').trim()).filter(Boolean);
}

/** What the box's lines would be refused for, or null. */
export function linesProblem(lines: string[], limits: HoldMusicLimits): string | null {
  if (lines.length > limits.max_lines) return `At most ${limits.max_lines} lines`;
  const long = lines.find((line) => line.length > limits.max_line_chars);
  return long ? `Each line can be at most ${limits.max_line_chars} characters` : null;
}

/** The file input's accept list: the extensions and their audio types. */
export function acceptList(limits: HoldMusicLimits): string {
  const mime: Record<string, string> = { wav: 'audio/wav', mp3: 'audio/mpeg', ogg: 'audio/ogg', m4a: 'audio/mp4' };
  return limits.types.flatMap((t) => [`.${t}`, mime[t]].filter(Boolean)).join(',');
}
