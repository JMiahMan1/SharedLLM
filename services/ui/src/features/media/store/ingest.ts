import type { MaPlayerListEntry } from '../../../lib/deviceLabels';
import { formatDeviceLabel } from '../../../lib/deviceLabels';
import type { MediaStatus } from '../types';
import type { NowPlayingItem, OutputId, PlaybackStatus, RemotePlayer, RepeatMode } from '../types';

/**
 * The pure reducer behind the media store: every way the app learns about a
 * remote player (the status poll, Music Assistant's player list, the server's
 * media event stream) goes through `ingest`, so the rules live in one place:
 *
 * - A new track replaces the metadata wholesale (BUG-51) and restarts the
 *   clock from the reported position (BUG-45).
 * - Position is an anchor (`positionSec` at `positionUpdatedAt`), never a
 *   ticking number; `livePosition` extrapolates it.
 * - For 1.5 s after a local volume change, reported volumes are ignored so
 *   the slider does not snap back (UX-38).
 */

export const VOLUME_GRACE_MS = 1500;

/** A player as the gateway's event stream reports it (`media_events.py`). */
export interface PlayerEventPayload {
  output_id: string;
  state?: string;
  item?: { uri?: string; title?: string; artists?: string[]; album?: string; image?: string; duration?: number | null; media_type?: string | null } | null;
  position?: number | null;
  position_updated_at?: string | number | null;
  volume?: number | null;
  muted?: boolean;
  shuffle?: boolean | null;
  repeat?: string | null;
  available?: boolean;
  group_members?: string[];
}

export type MediaEvent =
  /** `/execute/media/status`: every Home Assistant media player. */
  | { type: 'status'; players: MediaStatus[] }
  /** Music Assistant's `players/all`, minus this device's own web player. */
  | { type: 'ma-players'; players: MaPlayerListEntry[]; webPlayerId?: string | null }
  | { type: 'snapshot'; players: PlayerEventPayload[] }
  | ({ type: 'player' } & PlayerEventPayload)
  | { type: 'queue'; output_id: string; items_changed?: boolean; position?: number | null }
  | { type: 'heartbeat' };

export interface PlayersState {
  players: Record<OutputId, RemotePlayer>;
  /** When the person last moved each output's volume (ms epoch). */
  volumeTouchedAt: Record<OutputId, number>;
  /**
   * This browser's own Music Assistant player id (the Sendspin id), once the
   * app has seen it. The event stream forwards every MA player, including this
   * device's own, so the id is what lets `player`/`snapshot` drop it instead of
   * showing the person their own device as a separate speaker.
   */
  webPlayerId: string | null;
}

export const EMPTY_PLAYERS: PlayersState = { players: {}, volumeTouchedAt: {}, webPlayerId: null };

export function toStatus(state: string | undefined | null): PlaybackStatus {
  if (state === 'playing' || state === 'paused' || state === 'buffering') return state;
  return 'idle';
}

/** True when the item kind or URI identifies a radio/live stream (UX-12). */
export function isLiveItem(mediaType?: string | null, uri?: string | null): boolean {
  return Boolean(
    (mediaType && /^(radio|stream|live)$/i.test(mediaType))
    || (uri && /:\/\/radio(\/|$)/i.test(uri)),
  );
}

function isAvailable(state: string | undefined | null): boolean {
  return state !== 'unavailable' && state !== 'unknown';
}

function toRepeat(value: unknown): RepeatMode | null {
  return value === 'off' || value === 'all' || value === 'one' ? value : null;
}

/** Home Assistant reports volume 0..1, Music Assistant 0..100. */
function toVolume(id: OutputId, value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  const scaled = id.startsWith('ma:') ? value : value * 100;
  return Math.max(0, Math.min(100, Math.round(scaled)));
}

function toTime(value: unknown, now: number): number | null {
  if (value === null || value === undefined || value === '') return null;
  const ms = typeof value === 'number' ? (value > 1e12 ? value : value * 1000) : Date.parse(String(value));
  if (!Number.isFinite(ms)) return null;
  return Math.min(ms, now); // a server clock ahead of ours must not move time backwards
}

function blankPlayer(id: OutputId): RemotePlayer {
  return {
    id,
    kind: id.startsWith('ma:') ? 'ma' : 'ha',
    name: id,
    available: true,
    status: 'idle',
    item: null,
    positionSec: 0,
    positionUpdatedAt: 0,
    volume: null,
    muted: false,
    shuffle: null,
    repeat: null,
    groupMembers: [],
    iconKind: 'speaker',
    maPlayerId: null,
    appName: null,
    queueVersion: 0,
  };
}

/** The fields an update may carry; undefined means "not reported". */
interface PlayerPatch {
  name?: string;
  status?: PlaybackStatus;
  available?: boolean;
  item?: NowPlayingItem | null;
  position?: number | null;
  positionAt?: number | null;
  volume?: number | null;
  muted?: boolean;
  shuffle?: boolean | null;
  repeat?: RepeatMode | null;
  groupMembers?: string[];
  iconKind?: 'tv' | 'speaker';
  maPlayerId?: string | null;
  appName?: string | null;
}

function sameTrack(a: NowPlayingItem | null, b: NowPlayingItem | null): boolean {
  if (!a || !b) return a === b;
  if (a.uri && b.uri) return a.uri === b.uri && a.title === b.title;
  return a.title === b.title;
}

function applyPatch(prev: RemotePlayer, patch: PlayerPatch, now: number, volumeLocked: boolean): RemotePlayer {
  const next: RemotePlayer = { ...prev };
  if (patch.name) next.name = patch.name;
  if (patch.status !== undefined) next.status = patch.status;
  if (patch.available !== undefined) next.available = patch.available;
  if (patch.shuffle !== undefined) next.shuffle = patch.shuffle;
  if (patch.repeat !== undefined) next.repeat = patch.repeat;
  if (patch.groupMembers !== undefined) next.groupMembers = patch.groupMembers;
  if (patch.iconKind !== undefined) next.iconKind = patch.iconKind;
  if (patch.maPlayerId !== undefined) next.maPlayerId = patch.maPlayerId;
  if (patch.appName !== undefined) next.appName = patch.appName;

  const trackChanged = patch.item !== undefined && !sameTrack(prev.item, patch.item);
  if (patch.item !== undefined) next.item = patch.item; // wholesale, never merged (BUG-51)

  if (trackChanged) {
    // A new track restarts the clock even when the device reports 0 (BUG-45).
    next.positionSec = Math.max(0, patch.position ?? 0);
    next.positionUpdatedAt = patch.positionAt ?? now;
  } else if (typeof patch.position === 'number') {
    if (patch.positionAt != null) {
      next.positionSec = Math.max(0, patch.position);
      next.positionUpdatedAt = patch.positionAt;
    } else if (patch.position !== prev.positionSec || prev.positionUpdatedAt === 0) {
      // No timestamp: a changed reading is "now"; an unchanged one is the
      // same anchor again and must not rewind the running clock.
      next.positionSec = Math.max(0, patch.position);
      next.positionUpdatedAt = now;
    }
  }
  // Pausing freezes the clock where it is.
  if (prev.status === 'playing' && next.status !== 'playing' && !trackChanged && typeof patch.position !== 'number') {
    next.positionSec = livePosition(prev, now);
    next.positionUpdatedAt = now;
  }

  if (!volumeLocked) {
    if (patch.volume !== undefined && patch.volume !== null) next.volume = patch.volume;
    if (patch.muted !== undefined) next.muted = patch.muted;
  }
  return next;
}

function samePlayer(a: RemotePlayer, b: RemotePlayer): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function upsert(state: PlayersState, id: OutputId, patch: PlayerPatch, now: number): Record<OutputId, RemotePlayer> {
  const prev = state.players[id] ?? blankPlayer(id);
  const locked = now - (state.volumeTouchedAt[id] ?? 0) < VOLUME_GRACE_MS;
  const next = applyPatch(prev, patch, now, locked);
  // An update that changes nothing keeps the same object, so subscribers
  // (and React) see no change at all.
  if (state.players[id] && samePlayer(prev, next)) return state.players;
  return { ...state.players, [id]: next };
}

/** `next` unless it holds exactly the same player objects as `prev`. */
function stablePlayers(prev: Record<OutputId, RemotePlayer>, next: Record<OutputId, RemotePlayer>): Record<OutputId, RemotePlayer> {
  const keys = Object.keys(next);
  if (keys.length !== Object.keys(prev).length) return next;
  return keys.every((k) => prev[k] === next[k]) ? prev : next;
}

function patchFromStatus(row: MediaStatus, now: number): PlayerPatch {
  const id = row.entity_id ?? '';
  const hasItem = Boolean(row.media_title || row.media_content_id);
  return {
    name: formatDeviceLabel(row.friendly_name, id),
    status: toStatus(row.state),
    available: isAvailable(row.state),
    item: hasItem
      ? {
          uri: row.media_content_id ?? '',
          title: row.media_title ?? '',
          artist: row.media_artist ?? '',
          album: row.media_album ?? '',
          image: row.entity_picture ?? '',
          durationSec: Number(row.duration) || 0,
          isLive: isLiveItem(row.media_type, row.media_content_id),
        }
      : null,
    position: typeof row.position === 'number' ? row.position : null,
    positionAt: toTime(row.media_position_updated_at, now),
    volume: toVolume(id, row.volume_level),
    muted: typeof row.is_volume_muted === 'boolean' ? row.is_volume_muted : undefined,
    shuffle: typeof row.shuffle === 'boolean' ? row.shuffle : null,
    repeat: toRepeat(row.repeat),
    groupMembers: row.group_members ?? [],
    iconKind: row.icon_kind === 'tv' ? 'tv' : 'speaker',
    maPlayerId: row.ma_player_id ?? null,
    appName: row.app_name ?? null,
  };
}

function patchFromEvent(e: PlayerEventPayload, now: number): PlayerPatch {
  const item = e.item === undefined
    ? undefined
    : e.item === null
      ? null
      : {
          uri: e.item.uri ?? '',
          title: e.item.title ?? '',
          artist: (e.item.artists ?? []).filter(Boolean).join(', '),
          album: e.item.album ?? '',
          image: e.item.image ?? '',
          durationSec: Number(e.item.duration) || 0,
          isLive: isLiveItem(e.item.media_type, e.item.uri),
        };
  return {
    status: e.state !== undefined ? toStatus(e.state) : undefined,
    available: e.available,
    item,
    position: typeof e.position === 'number' ? e.position : null,
    positionAt: toTime(e.position_updated_at, now),
    volume: toVolume(e.output_id, e.volume),
    muted: e.muted,
    shuffle: e.shuffle ?? undefined,
    repeat: e.repeat !== undefined ? toRepeat(e.repeat) : undefined,
    groupMembers: e.group_members,
  };
}

/** Fold one event into the players state. Pure: same input, same output. */
export function ingest(state: PlayersState, event: MediaEvent, now: number): PlayersState {
  switch (event.type) {
    case 'status': {
      // The poll lists every Home Assistant player there is: ones it no
      // longer lists are gone. Music Assistant-only players are untouched.
      let players: Record<OutputId, RemotePlayer> = Object.fromEntries(
        Object.entries(state.players).filter(([, p]) => p.kind === 'ma'),
      );
      for (const row of event.players) {
        if (!row.entity_id || row.entity_id === 'web_player') continue;
        const id = row.entity_id;
        const base = { ...state, players: { ...players, [id]: state.players[id] ?? blankPlayer(id) } };
        players = upsert(base, id, patchFromStatus(row, now), now);
      }
      players = stablePlayers(state.players, players);
      return players === state.players ? state : { ...state, players };
    }
    case 'ma-players': {
      let players: Record<OutputId, RemotePlayer> = Object.fromEntries(
        Object.entries(state.players).filter(([, p]) => p.kind === 'ha'),
      );
      for (const p of event.players) {
        if (!p.player_id || p.player_id === event.webPlayerId) continue;
        const id = `ma:${p.player_id}`;
        const base = { ...state, players: { ...players, [id]: state.players[id] ?? blankPlayer(id) } };
        players = upsert(base, id, {
          name: p.name,
          status: toStatus(p.state),
          available: p.available,
          maPlayerId: p.player_id,
        }, now);
      }
      players = stablePlayers(state.players, players);
      const webPlayerId = event.webPlayerId ?? state.webPlayerId;
      if (players === state.players && webPlayerId === state.webPlayerId) return state;
      return { ...state, players, webPlayerId };
    }
    case 'snapshot': {
      let next = state;
      for (const p of event.players) next = ingest(next, { type: 'player', ...p }, now);
      return next;
    }
    case 'player': {
      if (!event.output_id || event.output_id === 'web_player') return state;
      // This device's own MA player is not a remote output: the event stream
      // forwards it too, so drop it here rather than let a later player_updated
      // re-add what the `ma-players` list already excluded.
      if (state.webPlayerId && event.output_id === `ma:${state.webPlayerId}`) return state;
      const players = upsert(state, event.output_id, patchFromEvent(event, now), now);
      return players === state.players ? state : { ...state, players };
    }
    case 'queue': {
      const prev = state.players[event.output_id];
      if (!prev) return state;
      const next = { ...prev, queueVersion: prev.queueVersion + (event.items_changed === false ? 0 : 1) };
      if (typeof event.position === 'number') {
        next.positionSec = Math.max(0, event.position);
        next.positionUpdatedAt = now;
      }
      return { ...state, players: { ...state.players, [event.output_id]: next } };
    }
    default:
      return state;
  }
}

/** The person moved `id`'s volume: show it at once and hold it for the grace period. */
export function touchVolume(state: PlayersState, id: OutputId, volume: number, now: number): PlayersState {
  const prev = state.players[id];
  if (!prev) return state;
  return {
    ...state,
    players: { ...state.players, [id]: { ...prev, volume, muted: false } },
    volumeTouchedAt: { ...state.volumeTouchedAt, [id]: now },
  };
}

export function touchMute(state: PlayersState, id: OutputId, muted: boolean, now: number): PlayersState {
  const prev = state.players[id];
  if (!prev) return state;
  return {
    ...state,
    players: { ...state.players, [id]: { ...prev, muted } },
    volumeTouchedAt: { ...state.volumeTouchedAt, [id]: now },
  };
}

/** Where the playhead is at `now`, from the anchor. */
export function livePosition(p: Pick<RemotePlayer, 'status' | 'positionSec' | 'positionUpdatedAt' | 'item'>, now: number): number {
  if (p.status !== 'playing' || !p.positionUpdatedAt) return p.positionSec;
  const elapsed = Math.max(0, (now - p.positionUpdatedAt) / 1000);
  const pos = p.positionSec + elapsed;
  const duration = p.item?.durationSec ?? 0;
  return duration > 0 ? Math.min(duration, pos) : pos;
}
