import { describe, expect, it } from 'vitest';
import {
  cameraStatusLine, changedCameras, recordableCameras, recorderStatusLine, sensitivityLabel,
  type CameraRecordingSettings, type MotionRecordingSettings, type RecordingCamera,
} from './cameraRecording';

const DEFAULTS: CameraRecordingSettings = {
  enabled: false, sensitivity: 60, pre_s: 3, post_s: 10, max_s: 120, fps: 4, retention_days: 14, schedule: null,
};

const cam = (over: Partial<RecordingCamera> = {}): RecordingCamera => ({
  entity_id: 'camera.shed', name: 'Shed', platform: 'esphome', records_itself: false, unavailable_reason: null,
  settings: { ...DEFAULTS }, motion_entity: null, status: null, ...over,
});

const settings = (over: Partial<MotionRecordingSettings> = {}): MotionRecordingSettings => ({
  configured: true, enabled: true, state: 'loaded', reason: null, account: 'household', household_account: null,
  url: '', username: '', has_password: true, folder: 'Jarvis/Cameras', defaults: DEFAULTS,
  limits: { sensitivity: [1, 100], pre_s: [0, 10], post_s: [2, 60], max_s: [10, 600], fps: [1, 10], retention_days: [1, 365] },
  error: null, cameras: [], ...over,
});

describe('motion recording helpers', () => {
  it('leaves out cameras that record themselves', () => {
    const s = settings({ cameras: [cam(), cam({ entity_id: 'camera.yard', records_itself: true })] });
    expect(recordableCameras(s).map((c) => c.entity_id)).toEqual(['camera.shed']);
  });

  it('says how the recorder is doing', () => {
    expect(recorderStatusLine(settings({ configured: false }))).toBe('Not set up yet.');
    expect(recorderStatusLine(settings({ enabled: false }))).toBe('Turned off.');
    expect(recorderStatusLine(settings())).toBe('Running.');
    expect(recorderStatusLine(settings({ error: 'Upload failed' }))).toBe('Running. Upload failed');
    expect(recorderStatusLine(settings({ state: 'setup_retry', reason: 'Nextcloud did not accept the login' })))
      .toBe('Not running: Nextcloud did not accept the login');
  });

  it('says what a camera is doing', () => {
    const now = new Date('2026-11-13T10:00:00Z');
    const on = { ...DEFAULTS, enabled: true };
    const status = { watching: true, motion: false, recording: false, error: null, clips: 2, last_clip: null,
      last_motion: '2026-11-13T09:55:00Z' };
    expect(cameraStatusLine(cam(), now)).toBe('Not recording.');
    expect(cameraStatusLine(cam({ settings: on }), now)).toBe('Starting…');
    expect(cameraStatusLine(cam({ settings: on, status }), now))
      .toBe('Watching for motion. Last motion 5 min ago. 2 clips since it started.');
    expect(cameraStatusLine(cam({ settings: on, status: { ...status, recording: true } }), now)).toBe('Recording motion now.');
    expect(cameraStatusLine(cam({ settings: on, status: { ...status, watching: false, error: 'sent no image' } }), now))
      .toBe('No picture: sent no image');
  });

  it('names the sensitivity in words', () => {
    expect(sensitivityLabel(90)).toMatch(/^High/);
    expect(sensitivityLabel(60)).toBe('Medium');
    expect(sensitivityLabel(20)).toMatch(/^Low/);
  });

  it('sends only what was changed', () => {
    const read = [cam(), cam({ entity_id: 'camera.printer' })];
    const edited = {
      'camera.shed': { ...DEFAULTS, enabled: true, schedule: { from: '22:00', to: '06:00' } },
      'camera.printer': { ...DEFAULTS },
    };
    expect(changedCameras(read, edited)).toEqual({
      'camera.shed': { enabled: true, schedule: { from: '22:00', to: '06:00' } },
    });
  });
});
