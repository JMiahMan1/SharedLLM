import { beforeEach, describe, expect, it } from 'vitest';
import type { MediaStatus } from '../types';
import {
  EMPTY_PLAYERS, ingest, livePosition, toStatus, touchMute, touchVolume, VOLUME_GRACE_MS,
  type MediaEvent, type PlayersState,
} from '../store/ingest';
import { MAX_RECENT_SEARCHES, resetMediaStore, useMediaStore } from '../store/mediaStore';
import { deviceOptions, playingElsewhere, selectPlayer } from '../store/selectors';

const T0 = Date.parse('2026-10-24T12:00:00Z');

const kitchen = (over: Partial<MediaStatus> = {}): MediaStatus => ({
  entity_id: 'media_player.kitchen',
  friendly_name: 'Kitchen Speaker',
  state: 'playing',
  media_title: 'Song A',
  media_artist: 'Artist A',
  media_album: 'Album A',
  media_content_id: 'library://track/1',
  entity_picture: '/art/a.jpg',
  position: 10,
  duration: 200,
  volume_level: 0.4,
  is_volume_muted: false,
  ...over,
});

const status = (...rows: MediaStatus[]): MediaEvent => ({ type: 'status', players: rows });

/** Apply `steps` (event, ms after T0) and return the final state. */
function run(steps: Array<[MediaEvent | ((s: PlayersState, now: number) => PlayersState), number]>): PlayersState {
  let s = EMPTY_PLAYERS;
  for (const [step, at] of steps) {
    s = typeof step === 'function' ? step(s, T0 + at) : ingest(s, step, T0 + at);
  }
  return s;
}

const K = 'media_player.kitchen';

describe('ingest: the media store reducer', () => {
  // Each row: a sequence of events, then what must be true at the end.
  const cases: Array<{ name: string; steps: Parameters<typeof run>[0]; at?: number; check: (s: PlayersState, now: number) => void }> = [
    {
      name: 'a status row becomes a named, playing player with 0-100 volume',
      steps: [[status(kitchen()), 0]],
      check: (s) => {
        expect(s.players[K]).toMatchObject({ name: 'Kitchen Speaker', status: 'playing', volume: 40, kind: 'ha', available: true });
        expect(s.players[K].item).toEqual({ uri: 'library://track/1', title: 'Song A', artist: 'Artist A', album: 'Album A', image: '/art/a.jpg', durationSec: 200 });
      },
    },
    {
      name: 'BUG-45 a new track restarts the clock from 0 even though the device says 0',
      steps: [[status(kitchen({ position: 150 })), 0], [status(kitchen({ media_content_id: 'library://track/2', media_title: 'Song B', position: 0 })), 5000]],
      check: (s) => {
        expect(s.players[K].positionSec).toBe(0);
        expect(s.players[K].positionUpdatedAt).toBe(T0 + 5000);
      },
    },
    {
      name: 'BUG-51 a new track replaces the metadata wholesale (no stale album or art)',
      steps: [[status(kitchen()), 0], [status(kitchen({ media_content_id: 'x://2', media_title: 'Song B', media_album: '', entity_picture: '' })), 1000]],
      check: (s) => expect(s.players[K].item).toMatchObject({ title: 'Song B', album: '', image: '' }),
    },
    {
      name: 'an unchanged poll keeps the running anchor (no clock rewind)',
      steps: [[status(kitchen({ position: 10 })), 0], [status(kitchen({ position: 10 })), 3000]],
      at: 3000,
      check: (s, now) => {
        expect(s.players[K].positionUpdatedAt).toBe(T0);
        expect(livePosition(s.players[K], now)).toBe(13);
      },
    },
    {
      name: 'Home Assistant position_updated_at anchors the clock where it was read',
      steps: [[status(kitchen({ position: 30, media_position_updated_at: new Date(T0 - 20_000).toISOString() })), 0]],
      at: 0,
      check: (s, now) => expect(livePosition(s.players[K], now)).toBe(50),
    },
    {
      name: 'a server clock ahead of ours never moves the playhead backwards',
      steps: [[status(kitchen({ position: 30, media_position_updated_at: new Date(T0 + 60_000).toISOString() })), 0]],
      check: (s) => expect(s.players[K].positionUpdatedAt).toBe(T0),
    },
    {
      name: 'pausing freezes the clock where it was',
      steps: [[status(kitchen({ position: 10 })), 0], [status(kitchen({ state: 'paused', position: undefined })), 7000]],
      at: 60_000,
      check: (s, now) => {
        expect(s.players[K].status).toBe('paused');
        expect(livePosition(s.players[K], now)).toBe(17);
      },
    },
    {
      name: 'UX-38 a reported volume is ignored right after the person moved the slider',
      steps: [[status(kitchen()), 0], [(s, now) => touchVolume(s, K, 80, now), 100], [status(kitchen({ volume_level: 0.4 })), 100 + VOLUME_GRACE_MS - 1]],
      check: (s) => expect(s.players[K].volume).toBe(80),
    },
    {
      name: 'after the grace period the device is the authority again',
      steps: [[status(kitchen()), 0], [(s, now) => touchVolume(s, K, 80, now), 100], [status(kitchen({ volume_level: 0.55 })), 100 + VOLUME_GRACE_MS + 1]],
      check: (s) => expect(s.players[K].volume).toBe(55),
    },
    {
      name: 'mute is held through the grace period too',
      steps: [[status(kitchen()), 0], [(s, now) => touchMute(s, K, true, now), 50], [status(kitchen({ is_volume_muted: false })), 500]],
      check: (s) => expect(s.players[K].muted).toBe(true),
    },
    {
      name: 'a player going unavailable is kept, marked offline and idle',
      steps: [[status(kitchen()), 0], [status(kitchen({ state: 'unavailable', media_title: '', media_content_id: '' })), 1000]],
      check: (s) => expect(s.players[K]).toMatchObject({ available: false, status: 'idle', item: null }),
    },
    {
      name: 'a player missing from the next poll is dropped',
      steps: [[status(kitchen(), { entity_id: 'media_player.den', friendly_name: 'Den', state: 'idle' }), 0], [status(kitchen()), 3000]],
      check: (s) => expect(Object.keys(s.players)).toEqual([K]),
    },
    {
      name: 'an event-stream player event updates state and item, keeping the name',
      steps: [[status(kitchen()), 0], [{ type: 'player', output_id: K, state: 'paused', item: { uri: 'library://track/1', title: 'Song A', artists: ['Artist A', 'Guest'], album: 'Album A', image: '/art/a.jpg', duration: 200 }, position: 42, position_updated_at: T0 + 500, volume: 0.3, muted: true }, 1000]],
      check: (s) => {
        expect(s.players[K]).toMatchObject({ name: 'Kitchen Speaker', status: 'paused', positionSec: 42, positionUpdatedAt: T0 + 500, volume: 30, muted: true });
        expect(s.players[K].item?.artist).toBe('Artist A, Guest');
      },
    },
    {
      name: 'a snapshot folds every player in; Music Assistant volumes are already 0-100',
      steps: [[{ type: 'snapshot', players: [{ output_id: 'ma:office', state: 'playing', volume: 35, item: null }, { output_id: K, state: 'idle' }] }, 0]],
      check: (s) => {
        expect(s.players['ma:office']).toMatchObject({ kind: 'ma', status: 'playing', volume: 35 });
        expect(s.players[K].status).toBe('idle');
      },
    },
    {
      name: 'queue events bump the queue version and can re-anchor the clock',
      steps: [[{ type: 'player', output_id: 'ma:office', state: 'playing' }, 0], [{ type: 'queue', output_id: 'ma:office', items_changed: true }, 100], [{ type: 'queue', output_id: 'ma:office', items_changed: false, position: 77 }, 200]],
      check: (s) => expect(s.players['ma:office']).toMatchObject({ queueVersion: 1, positionSec: 77, positionUpdatedAt: T0 + 200 }),
    },
    {
      name: 'Music Assistant player lists skip this device and replace earlier MA rows',
      steps: [
        [{ type: 'ma-players', webPlayerId: 'me', players: [{ player_id: 'me', name: 'Me', available: true, state: 'idle', powered: true }, { player_id: 'office', name: 'Office', available: true, state: 'playing', powered: true }] }, 0],
        [{ type: 'ma-players', players: [{ player_id: 'den', name: 'Den', available: false, state: 'idle', powered: false }] }, 100],
      ],
      check: (s) => {
        expect(Object.keys(s.players)).toEqual(['ma:den']);
        expect(s.players['ma:den']).toMatchObject({ name: 'Den', available: false, maPlayerId: 'den' });
      },
    },
    {
      name: 'this device’s own MA player is dropped from the event stream too (no phantom speaker)',
      steps: [
        [{ type: 'ma-players', webPlayerId: 'me', players: [{ player_id: 'office', name: 'Office', available: true, state: 'idle', powered: true }] }, 0],
        [{ type: 'player', output_id: 'ma:me', state: 'playing', item: { title: 'Mine', uri: 'x://m' } }, 1],
        [{ type: 'snapshot', players: [{ output_id: 'ma:me', state: 'playing', item: { title: 'Mine', uri: 'x://m' } }, { output_id: 'ma:office', state: 'playing' }] }, 2],
      ],
      check: (s) => {
        expect(s.webPlayerId).toBe('me');
        expect(s.players['ma:me']).toBeUndefined();
        expect(s.players['ma:office'].status).toBe('playing');
      },
    },
    {
      name: 'the web player row and empty ids are ignored',
      steps: [[status({ entity_id: 'web_player', state: 'playing' }, { state: 'playing' }), 0], [{ type: 'player', output_id: 'web_player', state: 'playing' }, 1], [{ type: 'heartbeat' }, 2], [{ type: 'queue', output_id: 'nobody' }, 3]],
      check: (s) => expect(s.players).toEqual({}),
    },
    {
      name: 'the favorite-less same-track update keeps one object (no re-render)',
      steps: [[status(kitchen()), 0]],
      check: (s) => {
        const again = ingest(s, status(kitchen()), T0);
        expect(again).toBe(s);
      },
    },
  ];

  it.each(cases)('$name', ({ steps, at, check }) => {
    check(run(steps), T0 + (at ?? steps[steps.length - 1][1]));
  });

  it('maps device states', () => {
    expect(['playing', 'paused', 'buffering', 'idle', 'off', 'standby', undefined].map(toStatus))
      .toEqual(['playing', 'paused', 'buffering', 'idle', 'idle', 'idle', 'idle']);
  });

  it('caps the live position at the track length and needs an anchor', () => {
    const p = { status: 'playing' as const, positionSec: 190, positionUpdatedAt: T0, item: { uri: '', title: '', artist: '', album: '', image: '', durationSec: 200 } };
    expect(livePosition(p, T0 + 60_000)).toBe(200);
    expect(livePosition({ ...p, positionUpdatedAt: 0 }, T0)).toBe(190);
    expect(livePosition({ ...p, item: null }, T0 + 5000)).toBe(195);
  });

  it('volume and mute touches ignore unknown outputs', () => {
    expect(touchVolume(EMPTY_PLAYERS, 'x', 5, T0)).toBe(EMPTY_PLAYERS);
    expect(touchMute(EMPTY_PLAYERS, 'x', true, T0)).toBe(EMPTY_PLAYERS);
  });
});

describe('useMediaStore', () => {
  beforeEach(() => resetMediaStore());
  const actions = () => useMediaStore.getState().actions;

  it('starts on this device', () => {
    expect(useMediaStore.getState()).toMatchObject({ activeOutputId: '', userPickedOutput: false });
  });

  it('follows a speaker that is already playing until the person picks', () => {
    actions().ingestStatus([kitchen({ state: 'idle' })], kitchen());
    expect(useMediaStore.getState()).toMatchObject({ activeOutputId: K, userPickedOutput: true });
    // The active row is merged over the list row.
    expect(useMediaStore.getState().players[K].status).toBe('playing');
  });

  it('does not follow once the person has picked this device', () => {
    actions().selectOutput('');
    actions().ingestStatus([], kitchen());
    expect(useMediaStore.getState().activeOutputId).toBe('');
    // An active row missing from the list is still known.
    expect(useMediaStore.getState().players[K]).toBeDefined();
  });

  it('persists only the output choice and recent searches', () => {
    actions().selectOutput('ma:office');
    actions().addRecentSearch('abba');
    const stored = JSON.parse(localStorage.getItem('jarvis_media') || '{}');
    expect(stored.state).toEqual({ activeOutputId: 'ma:office', userPickedOutput: true, recentSearches: ['abba'] });
  });

  it('keeps ten recent searches, newest first, without duplicates', () => {
    for (let i = 0; i < 12; i += 1) actions().addRecentSearch(`q${i}`);
    actions().addRecentSearch('Q5');
    actions().addRecentSearch('   ');
    const recent = useMediaStore.getState().recentSearches;
    expect(recent).toHaveLength(MAX_RECENT_SEARCHES);
    expect(recent[0]).toBe('Q5');
    expect(recent.filter((r) => r.toLowerCase() === 'q5')).toHaveLength(1);
    actions().clearRecentSearches();
    expect(useMediaStore.getState().recentSearches).toEqual([]);
  });

  it('volume, mute, optimistic patches, pending keys, sheets and the event state', () => {
    actions().ingestStatus([kitchen()]);
    actions().setVolume(K, 90);
    actions().setMuted(K, true);
    actions().patchPlayer(K, { status: 'paused' });
    actions().patchPlayer('nobody', { status: 'paused' });
    expect(useMediaStore.getState().players[K]).toMatchObject({ volume: 90, muted: true, status: 'paused' });
    actions().setPending('transport:next', true);
    expect(useMediaStore.getState().pending).toEqual({ 'transport:next': true });
    actions().setPending('transport:next', false);
    expect(useMediaStore.getState().pending).toEqual({});
    actions().openSheet('queue');
    expect(useMediaStore.getState().sheets.queue).toBe(true);
    actions().closeSheet('queue');
    expect(useMediaStore.getState().sheets.queue).toBe(false);
    actions().setEvents('open');
    expect(useMediaStore.getState().events).toBe('open');
    actions().ingest({ type: 'player', output_id: K, state: 'playing' });
    expect(selectPlayer(K)(useMediaStore.getState())?.status).toBe('playing');
    expect(selectPlayer('')(useMediaStore.getState())).toBeNull();
  });
});

describe('selectors', () => {
  const players = run([
    [status(kitchen({ ma_player_id: 'kitchen' }), { entity_id: 'media_player.den_tv', friendly_name: 'Den TV', state: 'idle', icon_kind: 'tv' }), 0],
    [{ type: 'ma-players', webPlayerId: 'me', players: [
      { player_id: 'kitchen', name: 'Kitchen', available: true, state: 'playing', powered: true },
      { player_id: 'office', name: 'Office', available: true, state: 'paused', powered: true },
    ] }, 0],
    [{ type: 'player', output_id: 'ma:office', state: 'paused', item: { title: 'Podcast', uri: 'x://p' } }, 0],
  ]).players;

  it('BUG-65 lists each speaker once and labels TVs', () => {
    const rows = deviceOptions([{ entity_id: K, friendly_name: 'Kitchen Speaker', state: 'playing' }], players, 'me');
    expect(rows.map((r) => r.id)).toEqual([K, 'media_player.den_tv', 'ma:office']);
    expect(rows.find((r) => r.id === 'media_player.den_tv')?.detail).toBe('TV');
    expect(rows.find((r) => r.id === K)?.detail).toBe('Playing');
  });

  it('lists what is playing around the house, playing first, each device once', () => {
    expect(playingElsewhere(players).map((p) => p.id)).toEqual([K, 'ma:office']);
  });

  it('never lists this device’s own MA player as elsewhere, even before it is named', () => {
    const own = { ...players['ma:office'], id: 'ma:me', name: 'This device' };
    const withOwn = { ...players, 'ma:me': own };
    expect(playingElsewhere(withOwn, 'me').map((p) => p.id)).toEqual([K, 'ma:office']);
    // Without the id the row is still visible; the caller is what names it.
    expect(playingElsewhere(withOwn).map((p) => p.id)).toContain('ma:me');
  });
});
