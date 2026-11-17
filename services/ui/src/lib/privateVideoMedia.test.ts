import { describe, expect, it } from 'vitest';
import {
  baseMime,
  formatDuration,
  isVideoFile,
  posterSize,
  RECORD_LIMIT_S,
  RECORDER_AUDIO_BPS,
  RECORDER_VIDEO_BPS,
  recorderMimeType,
  recorderMustStop,
  videoMimeOf,
  videoProblem,
} from './privateVideoMedia';
import { MAX_VIDEO_BYTES } from './privateChatCrypto';

describe('the recorder', () => {
  it('prefers MP4, then WebM, and says nothing when nothing is offered', () => {
    expect(recorderMimeType((t) => t.startsWith('video/mp4'))).toBe('video/mp4;codecs=avc1.42E01E,mp4a.40.2');
    expect(recorderMimeType((t) => t === 'video/mp4')).toBe('video/mp4');
    expect(recorderMimeType((t) => t.startsWith('video/webm'))).toBe('video/webm;codecs=vp9,opus');
    expect(recorderMimeType((t) => t === 'video/webm;codecs=vp8,opus')).toBe('video/webm;codecs=vp8,opus');
    expect(recorderMimeType(() => false)).toBe('');
    expect(recorderMimeType(undefined)).toBe('');
    // A build that records MP4 but cannot play it back records WebM instead.
    expect(recorderMimeType(() => true, (t) => (t.startsWith('video/mp4') ? '' : 'maybe'))).toBe('video/webm;codecs=vp9,opus');
    expect(recorderMimeType(() => {
      throw new Error('odd browser');
    })).toBe('');
  });

  it('three minutes at the asked-for rates fits the byte limit with room to spare', () => {
    const bytes = (RECORD_LIMIT_S * (RECORDER_VIDEO_BPS + RECORDER_AUDIO_BPS)) / 8;
    expect(RECORD_LIMIT_S).toBe(180);
    expect(bytes).toBeLessThan(MAX_VIDEO_BYTES * 0.8);
  });

  it('stops at three minutes, or just short of the byte limit, whichever comes first', () => {
    expect(recorderMustStop(0, 0)).toBeNull();
    expect(recorderMustStop(179.9, 1_000_000)).toBeNull();
    expect(recorderMustStop(180, 1_000_000)).toBe('time');
    expect(recorderMustStop(30, MAX_VIDEO_BYTES)).toBe('size');
    expect(recorderMustStop(30, MAX_VIDEO_BYTES - 3 * 1024 * 1024)).toBeNull();
  });
});

describe('picked clips', () => {
  it('knows a clip by its type, or by its name when the phone says nothing', () => {
    expect(videoMimeOf({ type: 'video/mp4', name: 'a.mp4' })).toBe('video/mp4');
    expect(videoMimeOf({ type: 'video/webm;codecs=vp8', name: '' })).toBe('video/webm');
    expect(videoMimeOf({ type: '', name: 'IMG_0001.MOV' })).toBe('video/quicktime');
    expect(videoMimeOf({ type: '', name: 'clip.3gp' })).toBe('video/3gpp');
    expect(videoMimeOf({ type: 'video/x-matroska', name: 'a.mkv' })).toBe('');
    expect(videoMimeOf({ type: 'image/png', name: 'a.mp4' })).toBe('');
    expect(isVideoFile({ type: 'video/x-matroska' })).toBe(true);
    expect(isVideoFile({ type: 'image/jpeg', name: 'x.jpg' })).toBe(false);
    expect(baseMime(' Video/MP4 ; codecs=avc1')).toBe('video/mp4');
  });

  it('refuses the wrong kind, an empty clip and one over the limit, with a reason', () => {
    expect(videoProblem({ type: 'video/mp4', size: 1000 })).toBeNull();
    expect(videoProblem({ type: 'video/mp4', size: MAX_VIDEO_BYTES })).toBeNull();
    expect(videoProblem({ type: 'video/mp4', size: MAX_VIDEO_BYTES + 1 })).toContain('limited to 50 MB');
    expect(videoProblem({ type: 'video/mp4', size: 0 })).toContain('empty');
    expect(videoProblem({ type: 'video/x-matroska', size: 10 })).toContain('cannot be sent');
  });
});

describe('labels and the poster', () => {
  it('formats a length like a phone does', () => {
    expect(formatDuration(0)).toBe('0:00');
    expect(formatDuration(7.4)).toBe('0:07');
    expect(formatDuration(179.6)).toBe('3:00');
    expect(formatDuration(3723)).toBe('1:02:03');
    expect(formatDuration(Number.NaN)).toBe('0:00');
  });

  it('scales the poster down to 640 on its longest side, never up', () => {
    expect(posterSize(1920, 1080)).toEqual({ width: 640, height: 360 });
    expect(posterSize(1080, 1920)).toEqual({ width: 360, height: 640 });
    expect(posterSize(320, 240)).toEqual({ width: 320, height: 240 });
    expect(posterSize(0, 0)).toEqual({ width: 640, height: 360 });
  });
});
