import { apiClient } from '../services/api';

/**
 * Motion recording for cameras that cannot record themselves (an ESP32 camera,
 * a 3D printer's MJPEG camera): the hub watches the picture for motion, keeps
 * clips in Nextcloud, and they show in the camera's Recordings tab like a
 * Reolink camera's. Admins set it up here (services/homehub/camera_recording.py).
 */

export interface RecordingSchedule { from: string; to: string }

export interface CameraRecordingSettings {
  enabled: boolean;
  /** 1-100: higher notices smaller movement. */
  sensitivity: number;
  /** Seconds kept from before the motion. */
  pre_s: number;
  /** Seconds the clip runs on after the motion stops. */
  post_s: number;
  /** The longest one clip may be. */
  max_s: number;
  /** Pictures a second watched and recorded. */
  fps: number;
  retention_days: number;
  /** Record only between these local times; null records always. */
  schedule: RecordingSchedule | null;
}

export interface CameraRecordingStatus {
  watching: boolean;
  motion: boolean;
  recording: boolean;
  error: string | null;
  clips: number;
  last_clip: string | null;
  last_motion: string | null;
}

export interface RecordingCamera {
  entity_id: string;
  name: string;
  platform: string;
  /** Keeps its own recordings (a Reolink camera): nothing to set here. */
  records_itself: boolean;
  /** Why the hub cannot record it (a battery camera), or null. */
  unavailable_reason: string | null;
  settings: CameraRecordingSettings;
  /** The motion sensor automations can use, once the camera has been watched. */
  motion_entity: string | null;
  status: CameraRecordingStatus | null;
}

export type RecordingNumber = 'sensitivity' | 'pre_s' | 'post_s' | 'max_s' | 'fps' | 'retention_days';

export interface MotionRecordingSettings {
  configured: boolean;
  enabled: boolean;
  state: string | null;
  reason: string | null;
  account: 'household' | 'custom' | null;
  /** Jarvis's own Nextcloud account, when it has one. */
  household_account: { url: string; username: string } | null;
  url: string;
  username: string;
  has_password: boolean;
  folder: string;
  defaults: CameraRecordingSettings;
  limits: Record<RecordingNumber, [number, number]>;
  error: string | null;
  cameras: RecordingCamera[];
}

export type MotionRecordingChange = Partial<{
  account: 'household' | 'custom';
  url: string;
  username: string;
  password: string;
  folder: string;
  enabled: boolean;
  cameras: Record<string, Partial<CameraRecordingSettings> | null>;
}>;

const SETTINGS = '/api/admin/cameras/recording';

export async function getMotionRecordingSettings(): Promise<MotionRecordingSettings> {
  return (await apiClient.get<MotionRecordingSettings>(SETTINGS)).data;
}

export async function saveMotionRecordingSettings(change: MotionRecordingChange): Promise<MotionRecordingSettings> {
  // Saving starts the recorder, which first checks Nextcloud: allow for a slow answer.
  return (await apiClient.put<MotionRecordingSettings>(SETTINGS, change, { timeout: 60_000 })).data;
}

/** Cameras the hub can record for: not ones that record themselves. */
export function recordableCameras(s: MotionRecordingSettings): RecordingCamera[] {
  return s.cameras.filter((c) => !c.records_itself);
}

/** One line on how the recorder is doing. */
export function recorderStatusLine(s: MotionRecordingSettings): string {
  if (!s.configured) return 'Not set up yet.';
  if (!s.enabled) return 'Turned off.';
  if (s.state === 'loaded') return s.error ? `Running. ${s.error}` : 'Running.';
  return s.reason ? `Not running: ${s.reason}` : 'Starting…';
}

/** One line on a camera: watching, recording now, its last motion. */
export function cameraStatusLine(c: RecordingCamera, now: Date = new Date()): string {
  const st = c.status;
  if (!c.settings.enabled || !st) return c.settings.enabled ? 'Starting…' : 'Not recording.';
  if (st.error && !st.watching) return `No picture: ${st.error}`;
  if (st.recording) return 'Recording motion now.';
  const parts = ['Watching for motion.'];
  if (st.last_motion) parts.push(`Last motion ${ago(new Date(st.last_motion), now)}.`);
  if (st.clips) parts.push(`${st.clips} clip${st.clips === 1 ? '' : 's'} since it started.`);
  return parts.join(' ');
}

function ago(when: Date, now: Date): string {
  const s = Math.max(0, Math.round((now.getTime() - when.getTime()) / 1000));
  if (Number.isNaN(s)) return 'a while ago';
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86_400) return `${Math.round(s / 3600)} h ago`;
  return when.toLocaleDateString();
}

/** What sensitivity means in words. */
export function sensitivityLabel(value: number): string {
  if (value >= 80) return 'High: small movement counts';
  if (value >= 45) return 'Medium';
  return 'Low: only large movement';
}

/** The settings that differ from what was read, per camera; empty when nothing changed. */
export function changedCameras(
  read: RecordingCamera[], edited: Record<string, CameraRecordingSettings>,
): Record<string, Partial<CameraRecordingSettings>> {
  const out: Record<string, Partial<CameraRecordingSettings>> = {};
  for (const cam of read) {
    const now = edited[cam.entity_id];
    if (!now) continue;
    const diff: Partial<CameraRecordingSettings> = {};
    for (const key of Object.keys(now) as (keyof CameraRecordingSettings)[]) {
      if (JSON.stringify(now[key]) !== JSON.stringify(cam.settings[key])) {
        (diff as Record<string, unknown>)[key] = now[key];
      }
    }
    if (Object.keys(diff).length) out[cam.entity_id] = diff;
  }
  return out;
}
