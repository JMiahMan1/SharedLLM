import { api } from '../../../../services/api';
import { sendMaRemoteCommand } from '../../../../lib/maRemotePlayer';
import type { PlayRequest } from '../../types';
import type { SharedPlayer } from '../../../../context/webPlayerContext';
import { outputKind, type OutputId, type RepeatMode } from '../../types';
import { assertSucceeded, parseQueueModes, type Capabilities, type PlayerAdapter } from './PlayerAdapter';

/**
 * Music Assistant only plays its own URIs; an Audiobookshelf item is mapped
 * server-side (a guessed URI leaves the player silent).
 */
export async function maUriFor(req: PlayRequest): Promise<string> {
  return req.kind === 'abs' ? (await api.resolveMALibraryUri(req.id, req.title)).ma_uri : req.uri;
}

const ALL: Capabilities = { queue: true, seek: true, shuffle: true, repeat: true, speed: false };

/** The phone/browser itself, through the shared web player (Sendspin + its JSON-RPC socket). */
export type WebPlayerHandle = Pick<
  SharedPlayer,
  'play' | 'enqueue' | 'pause' | 'next' | 'previous' | 'stop' | 'seek' | 'setVolume' | 'setMuted' | 'maCommand' | 'playerId'
>;

export function webPlayerAdapter(web: WebPlayerHandle): PlayerAdapter {
  const queueId = () => {
    if (!web.playerId) throw new Error('This device is not connected yet');
    return web.playerId;
  };
  return {
    kind: 'web',
    play: async (_id, req) => web.play(await maUriFor(req)),
    enqueue: async (_id, req, option) => web.enqueue(await maUriFor(req), option),
    transport: async (_id, command) => {
      if (command === 'pause') await web.pause();
      else if (command === 'resume') await web.play();
      else if (command === 'next') await web.next();
      else if (command === 'previous') await web.previous();
      else await web.stop();
    },
    seek: async (_id, seconds) => web.seek(seconds),
    setVolume: async (_id, volume) => web.setVolume(volume),
    setMuted: async (_id, muted) => web.setMuted(muted),
    setShuffle: async (_id, on) => {
      await web.maCommand('player_queues/shuffle', { queue_id: queueId(), shuffle_enabled: on });
    },
    setRepeat: async (_id, mode) => {
      await web.maCommand('player_queues/repeat', { queue_id: queueId(), repeat_mode: mode });
    },
    queueModes: async () => parseQueueModes(await web.maCommand('player_queues/get', { queue_id: queueId() })),
    capabilities: () => ({ ...ALL, speed: true }),
  };
}

/** A Music Assistant player Home Assistant does not expose (`ma:<player_id>`), via the gateway. */
export const maPlayerAdapter: PlayerAdapter = {
  kind: 'ma',
  play: async (id, req) => {
    await sendMaRemoteCommand('player_queues/play_media', { queue_id: maId(id), media: [await maUriFor(req)], option: 'replace' });
  },
  enqueue: async (id, req, option) => {
    await sendMaRemoteCommand('player_queues/play_media', { queue_id: maId(id), media: [await maUriFor(req)], option });
  },
  transport: async (id, command) => {
    const pid = maId(id);
    const cmd = {
      pause: 'players/cmd/pause', resume: 'players/cmd/play', stop: 'players/cmd/stop',
      next: 'player_queues/next', previous: 'player_queues/previous',
    }[command];
    await sendMaRemoteCommand(cmd, cmd.startsWith('player_queues/') ? { queue_id: pid } : { player_id: pid });
  },
  seek: async (id, seconds) => {
    await sendMaRemoteCommand('player_queues/seek', { queue_id: maId(id), position: Math.round(seconds) });
  },
  setVolume: async (id, volume) => {
    await sendMaRemoteCommand('players/cmd/volume_set', { player_id: maId(id), volume_level: volume });
  },
  setMuted: async (id, muted) => {
    await sendMaRemoteCommand('players/cmd/volume_mute', { player_id: maId(id), muted });
  },
  setShuffle: async (id, on) => {
    await sendMaRemoteCommand('player_queues/shuffle', { queue_id: maId(id), shuffle_enabled: on });
  },
  setRepeat: async (id, mode) => {
    await sendMaRemoteCommand('player_queues/repeat', { queue_id: maId(id), repeat_mode: mode });
  },
  queueModes: async (id) => parseQueueModes(await sendMaRemoteCommand('player_queues/get', { queue_id: maId(id) })),
  capabilities: () => ALL,
};

function maId(id: OutputId): string {
  return id.startsWith('ma:') ? id.slice(3) : id;
}

/**
 * A Home Assistant media player, through the execution service. A Music
 * Assistant item still fills the speaker's MA queue there (BUG-76); an
 * Audiobookshelf book starts an ABS session that resumes where it was.
 */
export const haPlayerAdapter: PlayerAdapter = {
  kind: 'ha',
  play: async (id, req) => {
    const resp = req.kind === 'abs'
      ? await api.playAudiobook({ book_id: req.id, entity_id: id, resume: true })
      : await api.mediaPlay({ entity_id: id, query: req.uri, media_type: 'music' });
    assertSucceeded(resp, 'Playback failed');
  },
  enqueue: async (id, req, option) => {
    const resp = await api.mediaPlay({ entity_id: id, query: await maUriFor(req), media_type: 'music', enqueue: option });
    assertSucceeded(resp, 'Could not add to the queue');
  },
  transport: async (id, command) => {
    assertSucceeded(await api.mediaTransport({ entity_id: id, command }), 'Command failed');
  },
  seek: async (id, seconds) => {
    assertSucceeded(await api.mediaTransport({ entity_id: id, command: 'seek', position: seconds }), 'Could not seek');
  },
  setVolume: async (id, volume) => {
    assertSucceeded(await api.mediaTransport({ entity_id: id, command: 'volume_set', volume_level: volume / 100 }), 'Could not change the volume');
  },
  setMuted: async (id, muted) => {
    assertSucceeded(await api.mediaTransport({ entity_id: id, command: 'volume_mute', muted }), 'Could not mute');
  },
  setShuffle: async (id, on) => {
    assertSucceeded(await api.mediaTransport({ entity_id: id, command: 'shuffle_set', shuffle: on }), 'Could not change shuffle');
  },
  setRepeat: async (id, mode: RepeatMode) => {
    assertSucceeded(await api.mediaTransport({ entity_id: id, command: 'repeat_set', repeat: mode }), 'Could not change repeat');
  },
  // Home Assistant reports shuffle/repeat in the player's state (the store has them).
  queueModes: async () => ({ shuffle: null, repeat: null }),
  capabilities: () => ALL,
};

/** The adapter for `outputId`: '' is this device, `ma:` a Music Assistant player, else Home Assistant. */
export function adapterFor(outputId: OutputId, web: WebPlayerHandle): PlayerAdapter {
  const kind = outputKind(outputId);
  return kind === 'web' ? webPlayerAdapter(web) : kind === 'ma' ? maPlayerAdapter : haPlayerAdapter;
}
