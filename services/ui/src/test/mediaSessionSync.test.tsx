import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render } from '@testing-library/react';

import MediaSessionSync from '../components/layout/MediaSessionSync';

const mediaSessionMock = vi.hoisted(() => ({
  hasAccess: vi.fn(),
  openSettings: vi.fn(),
  getActive: vi.fn(),
  nativeReports: undefined as undefined | ReturnType<typeof vi.fn>,
}));

vi.mock('../plugins/mediaSession', () => ({ default: mediaSessionMock }));

describe('MediaSessionSync', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('jarvis_api_key', 'test-key');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('leaves it to an APK whose notification listener reports plays itself', async () => {
    vi.resetModules();
    mediaSessionMock.nativeReports = vi.fn().mockResolvedValue({ media: true });
    mediaSessionMock.hasAccess.mockResolvedValue({ granted: true });
    mediaSessionMock.getActive.mockResolvedValue({ sessions: [{ app: 'com.spotify.music', title: 'Song', artist: '', album: '', playing: true, durationMs: 1, positionMs: 0 }] });
    const { default: Fresh } = await import('../components/layout/MediaSessionSync');

    render(<Fresh />);

    await vi.waitFor(() => expect(mediaSessionMock.nativeReports).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(mediaSessionMock.getActive).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
    mediaSessionMock.nativeReports = undefined;
    vi.resetModules();
  });

  it('reports a playing external session once', async () => {
    mediaSessionMock.hasAccess.mockResolvedValue({ granted: true });
    mediaSessionMock.getActive.mockResolvedValue({
      sessions: [
        { app: 'com.google.android.youtube', title: 'A Song', artist: 'Someone', album: '', playing: true, durationMs: 200000, positionMs: 1000 },
      ],
    });

    render(<MediaSessionSync />);

    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/activity');
    const body = JSON.parse(String(init.body));
    expect(body.feature).toBe('media');
    expect(body.event).toBe('play');
    expect(body.item).toBe('A Song');
    expect(body.detail.app).toBe('com.google.android.youtube');
    expect(body.detail.source).toBe('external');
  });

  it('stays quiet without notification access', async () => {
    mediaSessionMock.hasAccess.mockResolvedValue({ granted: false });
    mediaSessionMock.getActive.mockResolvedValue({ sessions: [] });

    render(<MediaSessionSync />);

    await vi.waitFor(() => expect(mediaSessionMock.hasAccess).toHaveBeenCalled());
    expect(fetch).not.toHaveBeenCalled();
  });

  it('ignores sessions that are not currently playing', async () => {
    mediaSessionMock.hasAccess.mockResolvedValue({ granted: true });
    mediaSessionMock.getActive.mockResolvedValue({
      sessions: [
        { app: 'com.google.android.youtube', title: 'Paused Video', artist: 'Someone', album: '', playing: false, durationMs: 200000, positionMs: 1000 },
      ],
    });

    render(<MediaSessionSync />);

    await vi.waitFor(() => expect(mediaSessionMock.getActive).toHaveBeenCalled());
    // Give the sync a tick to settle, then assert no fetch happened.
    await new Promise((r) => setTimeout(r, 50));
    expect(fetch).not.toHaveBeenCalled();
  });
});
