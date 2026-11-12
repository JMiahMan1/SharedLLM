import { afterEach, describe, expect, it } from 'vitest';
import {
  browserCaps, firstFrameTimeoutMs, mp4Url, mseSocketUrl, nextMode, noteModeFailed, noteModeWorked, pickQuality,
  playbackModes, resetModeMemory, supportedCodecs, webrtcUrl, type CameraRealtime,
} from './cameraLive';

const TOKEN = { token: 'mt-1', user: 'alice', expiresAt: 4_000_000_000 };
const RT: CameraRealtime = { modes: ['webrtc', 'mse', 'mp4'], qualities: ['sub', 'main'], default_quality: 'sub' };
const ALL = { webrtc: true, mse: true };

afterEach(() => resetModeMemory());

describe('real-time player choice', () => {
  it('plays nothing in real time when the hub does not offer it', () => {
    expect(playbackModes(undefined, ALL)).toEqual([]);
    expect(playbackModes(null, ALL)).toEqual([]);
    expect(playbackModes({ ...RT, qualities: [] }, ALL)).toEqual([]);
  });

  it('tries WebRTC, then MSE, then MP4, and only what the browser can play', () => {
    expect(playbackModes(RT, ALL)).toEqual(['webrtc', 'mse', 'mp4']);
    expect(playbackModes(RT, { webrtc: false, mse: true })).toEqual(['mse', 'mp4']);
    expect(playbackModes(RT, { webrtc: false, mse: false })).toEqual(['mp4']);
    // Only what the hub reports, in our order whatever its order.
    expect(playbackModes({ ...RT, modes: ['mp4', 'mse'] }, ALL)).toEqual(['mse', 'mp4']);
  });

  it('moves on to the next mode and then gives up', () => {
    const modes = playbackModes(RT, ALL);
    expect(nextMode(modes, 'webrtc')).toBe('mse');
    expect(nextMode(modes, 'mse')).toBe('mp4');
    expect(nextMode(modes, 'mp4')).toBeNull();
  });

  it('puts a mode that failed this session last, and forgives it once it works', () => {
    noteModeFailed('webrtc');
    expect(playbackModes(RT, ALL)).toEqual(['mse', 'mp4', 'webrtc']);
    noteModeWorked('webrtc');
    expect(playbackModes(RT, ALL)).toEqual(['webrtc', 'mse', 'mp4']);
  });

  it('starts at a quality the camera has', () => {
    expect(pickQuality(RT)).toBe('sub');
    expect(pickQuality(RT, 'main')).toBe('main');
    expect(pickQuality({ ...RT, qualities: ['main'], default_quality: 'sub' })).toBe('main');
  });

  it('waits longer for a battery camera to wake', () => {
    expect(firstFrameTimeoutMs(true)).toBeGreaterThan(firstFrameTimeoutMs(false));
    expect(firstFrameTimeoutMs(true)).toBeGreaterThanOrEqual(30_000);
  });

  it('reads what the browser supports', () => {
    expect(browserCaps({})).toEqual({ webrtc: false, mse: false });
    const MediaSource = Object.assign(function MediaSource() {}, { isTypeSupported: () => true });
    expect(browserCaps({ RTCPeerConnection: function PC() {}, MediaSource })).toEqual({ webrtc: true, mse: true });
    expect(browserCaps({ ManagedMediaSource: MediaSource })).toEqual({ webrtc: false, mse: true });
  });

  it('offers go2rtc only the codecs this browser plays', () => {
    expect(supportedCodecs((t) => t.includes('avc1') || t.includes('mp4a.40.2'))).toBe(
      'avc1.640029,avc1.64002A,avc1.640033,mp4a.40.2');
    expect(supportedCodecs(() => { throw new Error('no'); })).toBe('');
  });
});

describe('real-time URLs', () => {
  it('go through the gateway, signed with the media token, never go2rtc itself', () => {
    const w = new URL(webrtcUrl(TOKEN, 'camera.x', 'main', 'https://jarvis.example'));
    expect(w.pathname).toBe('/api/media/cameras/camera.x/live/webrtc');
    expect(Object.fromEntries(w.searchParams)).toEqual({ quality: 'main', user: 'alice', mt: 'mt-1' });
    expect(mseSocketUrl(TOKEN, 'camera.x', 'sub', 'https://jarvis.example/')).toBe(
      'wss://jarvis.example/api/media/cameras/camera.x/live/ws?quality=sub&user=alice&mt=mt-1');
    expect(mseSocketUrl(TOKEN, 'camera.x', 'sub', 'http://10.0.0.2:8080')).toMatch(/^ws:\/\/10\.0\.0\.2:8080\//);
    expect(mp4Url(TOKEN, 'camera.x', 'sub', 'https://jarvis.example')).toContain('/live/stream.mp4?quality=sub');
  });
});
