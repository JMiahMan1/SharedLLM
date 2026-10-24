import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  applySessionMetadata, applySessionPosition, applySessionState, ARTWORK_SIZES, bindSessionHandlers, sessionArtwork,
  SEEK_BACK_SECONDS, SEEK_FORWARD_SECONDS, type SessionState,
} from '../engine/mediaSession';
import { ensureMediaToken, resetMediaTokenForTests } from '../../../lib/mediaToken';

class Metadata {
  title: string; artist: string; album: string; artwork: MediaImage[];
  constructor(init: MediaMetadataInit) {
    this.title = init.title ?? '';
    this.artist = init.artist ?? '';
    this.album = init.album ?? '';
    this.artwork = [...(init.artwork ?? [])];
  }
}

function fakeSession() {
  const handlers = new Map<string, MediaSessionActionHandler | null>();
  return {
    metadata: null as MediaMetadata | null,
    playbackState: 'none' as MediaSessionPlaybackState,
    setPositionState: vi.fn(),
    setActionHandler: vi.fn((action: MediaSessionAction, handler: MediaSessionActionHandler | null) => {
      if (action === 'skipad') throw new Error('unsupported');
      handlers.set(action, handler);
    }),
    handlers,
  };
}

const PLAYING: SessionState = {
  title: 'Midnight Drive', artist: 'The Synthwave Collective', album: 'Neon Nights', image: 'http://ma/img/1',
  isPlaying: true, position: 42, duration: 210, playbackRate: 1.5,
};

beforeEach(async () => {
  vi.stubGlobal('MediaMetadata', Metadata);
  resetMediaTokenForTests();
  localStorage.setItem('jarvis_api_key', 'k');
  await ensureMediaToken();
});

describe('OS media session (BUG-56)', () => {
  it('sets metadata with proxied artwork at every size', () => {
    const session = fakeSession();
    applySessionState(PLAYING, session);
    const meta = session.metadata as unknown as Metadata;
    expect(meta).toMatchObject({ title: 'Midnight Drive', artist: 'The Synthwave Collective', album: 'Neon Nights' });
    expect(meta.artwork.map((a) => a.sizes)).toEqual(ARTWORK_SIZES.map((s) => `${s}x${s}`));
    for (const art of meta.artwork) {
      // Through the gateway proxy, signed with the media token, never the raw MA path.
      expect(art.src).toMatch(/\/api\/media\/imageproxy\?path=http%3A%2F%2Fma%2Fimg%2F1&w=\d+&user=alice&mt=mt-1$/);
    }
    expect(session.playbackState).toBe('playing');
    expect(session.setPositionState).toHaveBeenCalledWith({ duration: 210, position: 42, playbackRate: 1.5 });
  });

  it('reports paused, clamps the position, and skips the scrubber for live streams', () => {
    const session = fakeSession();
    applySessionState({ ...PLAYING, isPlaying: false, position: 500 }, session);
    expect(session.playbackState).toBe('paused');
    expect(session.setPositionState).toHaveBeenLastCalledWith({ duration: 210, position: 210, playbackRate: 1.5 });
    session.setPositionState.mockClear();
    applySessionPosition({ ...PLAYING, duration: 0 }, session);
    applySessionPosition({ ...PLAYING, playbackRate: 0, position: -3 }, session);
    expect(session.setPositionState).toHaveBeenCalledTimes(1);
    expect(session.setPositionState).toHaveBeenCalledWith({ duration: 210, position: 0, playbackRate: 1 });
  });

  it('clears everything when nothing is loaded', () => {
    const session = fakeSession();
    applySessionState(PLAYING, session);
    applySessionMetadata(null, session);
    expect(session.metadata).toBeNull();
    expect(session.playbackState).toBe('none');
  });

  it('has no artwork until the media token is held', () => {
    resetMediaTokenForTests();
    localStorage.removeItem('jarvis_api_key');
    expect(sessionArtwork('http://ma/img/1')).toEqual([]);
    expect(sessionArtwork(null)).toEqual([]);
  });

  it('routes every OS action, skipping 15 s back and 30 s forward', () => {
    const session = fakeSession();
    const h = {
      play: vi.fn(), pause: vi.fn(), stop: vi.fn(), next: vi.fn(), previous: vi.fn(), seekTo: vi.fn(),
      position: () => 100,
    };
    const unbind = bindSessionHandlers(h, session);
    const fire = (action: string, details: Partial<MediaSessionActionDetails> = {}) =>
      session.handlers.get(action)?.({ action, ...details } as MediaSessionActionDetails);
    fire('play'); fire('pause'); fire('stop'); fire('nexttrack'); fire('previoustrack');
    fire('seekto', { seekTime: 33 });
    fire('seekto', {});
    fire('seekbackward');
    fire('seekforward');
    fire('seekbackward', { seekOffset: 5 });
    expect(h.play).toHaveBeenCalled();
    expect(h.pause).toHaveBeenCalled();
    expect(h.stop).toHaveBeenCalled();
    expect(h.next).toHaveBeenCalled();
    expect(h.previous).toHaveBeenCalled();
    expect(h.seekTo.mock.calls.map((c) => c[0])).toEqual([33, 100 - SEEK_BACK_SECONDS, 100 + SEEK_FORWARD_SECONDS, 95]);
    unbind();
    expect([...session.handlers.values()].every((v) => v === null)).toBe(true);
  });

  it('does nothing without a media session', () => {
    applySessionState(PLAYING, null);
    expect(bindSessionHandlers({} as never, null)).toBeTypeOf('function');
  });

  it('survives a session that throws', () => {
    const session = fakeSession();
    session.setPositionState.mockImplementation(() => { throw new Error('bad'); });
    Object.defineProperty(session, 'metadata', { set: () => { throw new Error('nope'); } });
    expect(() => applySessionState(PLAYING, session)).not.toThrow();
  });
});
