import { beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '../../../test/setup';
import { adapterFor, haPlayerAdapter, maPlayerAdapter, maUriFor, webPlayerAdapter, type WebPlayerHandle } from '../engine/adapters/adapters';
import { nextRepeat, parseQueueModes } from '../engine/adapters/PlayerAdapter';

type Sent = { command: string; args: Record<string, unknown> };
let maSent: Sent[] = [];
let transports: Record<string, unknown>[] = [];
let plays: Record<string, unknown>[] = [];
let books: Record<string, unknown>[] = [];
let resolves: Record<string, unknown>[] = [];
let fail = false;

beforeEach(() => {
  maSent = []; transports = []; plays = []; books = []; resolves = []; fail = false;
  server.use(
    http.post('/api/media/ma-command', async ({ request }) => {
      const body = (await request.json()) as Sent;
      maSent.push(body);
      if (fail) return HttpResponse.json({ status: 'FAILURE', message: 'The player is not available' });
      if (body.command === 'player_queues/get') return HttpResponse.json({ status: 'SUCCESS', result: { shuffle_enabled: true, repeat_mode: 'one' } });
      return HttpResponse.json({ status: 'SUCCESS', result: null });
    }),
    http.post('/execute/media/transport', async ({ request }) => {
      transports.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json(fail ? { status: 'FAILURE', message: 'Entity is off' } : { status: 'SUCCESS' });
    }),
    http.post('/execute/media/play', async ({ request }) => {
      plays.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json(fail ? { status: 'FAILURE', message: 'No such item' } : { status: 'SUCCESS' });
    }),
    http.post('/execute/audiobookshelf', async ({ request }) => {
      books.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({ status: 'SUCCESS' });
    }),
    http.post('/api/media/ma-library-uri', async ({ request }) => {
      resolves.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({ ma_uri: 'library://audiobook/42', title: 'Book' });
    }),
  );
});

const TRACK = { kind: 'uri' as const, uri: 'library://track/7', title: 'Song' };
const BOOK = { kind: 'abs' as const, id: 'abs-1', title: 'Book' };

describe('Music Assistant speaker adapter', () => {
  const ID = 'ma:kitchen';
  it.each([
    ['play', () => maPlayerAdapter.play(ID, TRACK), { command: 'player_queues/play_media', args: { queue_id: 'kitchen', media: ['library://track/7'], option: 'replace' } }],
    ['play next', () => maPlayerAdapter.enqueue(ID, TRACK, 'next'), { command: 'player_queues/play_media', args: { queue_id: 'kitchen', media: ['library://track/7'], option: 'next' } }],
    ['add to queue', () => maPlayerAdapter.enqueue(ID, TRACK, 'add'), { command: 'player_queues/play_media', args: { queue_id: 'kitchen', media: ['library://track/7'], option: 'add' } }],
    ['pause', () => maPlayerAdapter.transport(ID, 'pause'), { command: 'players/cmd/pause', args: { player_id: 'kitchen' } }],
    ['resume', () => maPlayerAdapter.transport(ID, 'resume'), { command: 'players/cmd/play', args: { player_id: 'kitchen' } }],
    ['stop', () => maPlayerAdapter.transport(ID, 'stop'), { command: 'players/cmd/stop', args: { player_id: 'kitchen' } }],
    ['next', () => maPlayerAdapter.transport(ID, 'next'), { command: 'player_queues/next', args: { queue_id: 'kitchen' } }],
    ['previous', () => maPlayerAdapter.transport(ID, 'previous'), { command: 'player_queues/previous', args: { queue_id: 'kitchen' } }],
    ['seek', () => maPlayerAdapter.seek(ID, 61.6), { command: 'player_queues/seek', args: { queue_id: 'kitchen', position: 62 } }],
    ['volume', () => maPlayerAdapter.setVolume(ID, 35), { command: 'players/cmd/volume_set', args: { player_id: 'kitchen', volume_level: 35 } }],
    ['mute', () => maPlayerAdapter.setMuted(ID, true), { command: 'players/cmd/volume_mute', args: { player_id: 'kitchen', muted: true } }],
    ['shuffle', () => maPlayerAdapter.setShuffle(ID, true), { command: 'player_queues/shuffle', args: { queue_id: 'kitchen', shuffle_enabled: true } }],
    ['repeat', () => maPlayerAdapter.setRepeat(ID, 'all'), { command: 'player_queues/repeat', args: { queue_id: 'kitchen', repeat_mode: 'all' } }],
  ])('%s sends the exact command', async (_name, call, expected) => {
    await call();
    expect(maSent).toEqual([expected]);
  });

  it('resolves an Audiobookshelf book to its Music Assistant URI first', async () => {
    await maPlayerAdapter.play(ID, BOOK);
    expect(resolves).toEqual([{ abs_item_id: 'abs-1', title: 'Book' }]);
    expect(maSent[0].args.media).toEqual(['library://audiobook/42']);
  });

  it('reads shuffle and repeat from the queue', async () => {
    expect(await maPlayerAdapter.queueModes(ID)).toEqual({ shuffle: true, repeat: 'one' });
  });

  it('throws MA\'s refusal', async () => {
    fail = true;
    await expect(maPlayerAdapter.transport(ID, 'next')).rejects.toThrow('The player is not available');
  });
});

describe('Home Assistant speaker adapter', () => {
  const ID = 'media_player.loft';
  it.each([
    ['pause', () => haPlayerAdapter.transport(ID, 'pause'), { entity_id: ID, command: 'pause' }],
    ['resume', () => haPlayerAdapter.transport(ID, 'resume'), { entity_id: ID, command: 'resume' }],
    ['next', () => haPlayerAdapter.transport(ID, 'next'), { entity_id: ID, command: 'next' }],
    ['seek', () => haPlayerAdapter.seek(ID, 30), { entity_id: ID, command: 'seek', position: 30 }],
    ['volume (0..1 for HA)', () => haPlayerAdapter.setVolume(ID, 40), { entity_id: ID, command: 'volume_set', volume_level: 0.4 }],
    ['mute', () => haPlayerAdapter.setMuted(ID, false), { entity_id: ID, command: 'volume_mute', muted: false }],
    ['shuffle', () => haPlayerAdapter.setShuffle(ID, true), { entity_id: ID, command: 'shuffle_set', shuffle: true }],
    ['repeat', () => haPlayerAdapter.setRepeat(ID, 'one'), { entity_id: ID, command: 'repeat_set', repeat: 'one' }],
  ])('%s sends the exact transport', async (_name, call, expected) => {
    await call();
    expect(transports).toEqual([expected]);
  });

  it('plays a Music Assistant item through the execution service', async () => {
    await haPlayerAdapter.play(ID, TRACK);
    expect(plays).toEqual([{ entity_id: ID, query: 'library://track/7', media_type: 'music' }]);
  });

  it('queues next / at the end on the speaker\'s MA queue', async () => {
    await haPlayerAdapter.enqueue(ID, TRACK, 'next');
    expect(plays).toEqual([{ entity_id: ID, query: 'library://track/7', media_type: 'music', enqueue: 'next' }]);
  });

  it('starts an Audiobookshelf session for a book, resuming', async () => {
    await haPlayerAdapter.play(ID, BOOK);
    expect(books[0]).toMatchObject({ entity_id: ID, book_id: 'abs-1', resume: true });
  });

  it('turns a FAILURE answer into an error', async () => {
    fail = true;
    await expect(haPlayerAdapter.play(ID, TRACK)).rejects.toThrow('No such item');
    await expect(haPlayerAdapter.transport(ID, 'pause')).rejects.toThrow('Entity is off');
  });

  it('has no queue modes of its own (they come with its state)', async () => {
    expect(await haPlayerAdapter.queueModes(ID)).toEqual({ shuffle: null, repeat: null });
  });
});

describe('this device\'s adapter', () => {
  const web = (): WebPlayerHandle => ({
    playerId: 'me',
    play: vi.fn().mockResolvedValue(undefined),
    enqueue: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn().mockResolvedValue(undefined),
    next: vi.fn().mockResolvedValue(undefined),
    previous: vi.fn().mockResolvedValue(undefined),
    stop: vi.fn().mockResolvedValue(undefined),
    seek: vi.fn().mockResolvedValue(undefined),
    setVolume: vi.fn(),
    setMuted: vi.fn(),
    maCommand: vi.fn().mockResolvedValue({ shuffle_enabled: false, repeat_mode: 'off' }),
  });

  it('drives the shared web player directly', async () => {
    const w = web();
    const a = webPlayerAdapter(w);
    await a.play('', TRACK);
    await a.enqueue('', BOOK, 'add');
    await a.transport('', 'pause');
    await a.transport('', 'resume');
    await a.transport('', 'next');
    await a.transport('', 'previous');
    await a.transport('', 'stop');
    await a.seek('', 12);
    await a.setVolume('', 20);
    await a.setMuted('', true);
    await a.setShuffle('', true);
    await a.setRepeat('', 'all');
    expect(w.play).toHaveBeenNthCalledWith(1, 'library://track/7');
    expect(w.play).toHaveBeenNthCalledWith(2);
    expect(w.enqueue).toHaveBeenCalledWith('library://audiobook/42', 'add');
    expect(w.pause).toHaveBeenCalled();
    expect(w.next).toHaveBeenCalled();
    expect(w.previous).toHaveBeenCalled();
    expect(w.stop).toHaveBeenCalled();
    expect(w.seek).toHaveBeenCalledWith(12);
    expect(w.setVolume).toHaveBeenCalledWith(20);
    expect(w.setMuted).toHaveBeenCalledWith(true);
    expect(w.maCommand).toHaveBeenCalledWith('player_queues/shuffle', { queue_id: 'me', shuffle_enabled: true });
    expect(w.maCommand).toHaveBeenCalledWith('player_queues/repeat', { queue_id: 'me', repeat_mode: 'all' });
    expect(await a.queueModes('')).toEqual({ shuffle: false, repeat: 'off' });
    expect(a.capabilities('').speed).toBe(true);
  });

  it('refuses queue commands before the player has an id', async () => {
    await expect(webPlayerAdapter({ ...web(), playerId: '' }).setShuffle('', true)).rejects.toThrow('not connected');
  });

  it('adapterFor routes by output id', () => {
    const w = web();
    expect(adapterFor('', w).kind).toBe('web');
    expect(adapterFor('ma:x', w)).toBe(maPlayerAdapter);
    expect(adapterFor('media_player.x', w)).toBe(haPlayerAdapter);
  });
});

describe('helpers', () => {
  it('cycles repeat off → all → one → off', () => {
    expect(['off', 'all', 'one'].map((m) => nextRepeat(m as 'off'))).toEqual(['all', 'one', 'off']);
  });
  it('parses queue modes defensively', () => {
    expect(parseQueueModes(null)).toEqual({ shuffle: null, repeat: null });
    expect(parseQueueModes({ shuffle_enabled: 'yes', repeat_mode: 'sometimes' })).toEqual({ shuffle: null, repeat: null });
  });
  it('passes Music Assistant URIs through untouched', async () => {
    expect(await maUriFor(TRACK)).toBe('library://track/7');
    expect(resolves).toEqual([]);
  });
});
