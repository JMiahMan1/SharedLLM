import type { DeviceOption } from '../types';
import { formatDeviceLabel } from '../../../lib/deviceLabels';
import type { OutputId, RemotePlayer } from '../types';
import type { MediaStoreState } from './mediaStore';

/** A Home Assistant `media_player` entity as `/api/entities` lists it. */
export interface MediaEntity {
  entity_id: string;
  friendly_name?: string;
  state?: string;
}

export const selectActiveOutputId = (s: MediaStoreState) => s.activeOutputId;
export const selectPlayers = (s: MediaStoreState) => s.players;

export function selectPlayer(id: OutputId) {
  return (s: MediaStoreState): RemotePlayer | null => (id ? s.players[id] ?? null : null);
}

/**
 * The device picker's rows, each speaker once (BUG-65): Home Assistant
 * entities first, then Music Assistant players Home Assistant does not
 * already expose (matched by `ma_player_id`), never this device's own player.
 */
export function deviceOptions(
  entities: MediaEntity[],
  players: Record<OutputId, RemotePlayer>,
  webPlayerId: string | null | undefined,
): DeviceOption[] {
  const byId = new Map<string, DeviceOption>();
  const detail = (p: RemotePlayer | undefined, state: string | undefined) =>
    p?.iconKind === 'tv' ? 'TV' : (p?.status ?? state) === 'playing' ? 'Playing' : 'Speaker';
  for (const e of entities) {
    const p = players[e.entity_id];
    byId.set(e.entity_id, {
      id: e.entity_id,
      name: formatDeviceLabel(e.friendly_name, p?.name, e.entity_id),
      detail: detail(p, e.state),
      online: p ? p.available : e.state !== 'unavailable' && e.state !== 'unknown',
    });
  }
  const haPlayers = Object.values(players).filter((p) => p.kind === 'ha');
  for (const p of haPlayers) {
    if (byId.has(p.id)) continue;
    byId.set(p.id, { id: p.id, name: formatDeviceLabel(p.name, p.id), detail: detail(p, undefined), online: p.available });
  }
  const covered = new Set<string>(haPlayers.map((p) => p.maPlayerId ?? '').filter(Boolean));
  for (const p of Object.values(players)) {
    if (p.kind !== 'ma' || !p.maPlayerId || covered.has(p.maPlayerId) || p.maPlayerId === webPlayerId) continue;
    byId.set(p.id, { id: p.id, name: p.name, detail: 'Music Assistant', online: p.available });
  }
  return [...byId.values()].sort((a, b) => Number(b.online) - Number(a.online));
}

/** Outputs other than this device with something loaded ("Playing in your home"). */
export function playingElsewhere(players: Record<OutputId, RemotePlayer>): RemotePlayer[] {
  const all = Object.values(players);
  // A Music Assistant player Home Assistant also exposes is shown once, as the entity.
  const covered = new Set(all.filter((p) => p.kind === 'ha' && p.maPlayerId).map((p) => p.maPlayerId));
  return all
    .filter((p) => !(p.kind === 'ma' && covered.has(p.maPlayerId)))
    // Something loaded: a title, or an app (a TV casting YouTube) that gives none.
    .filter((p) => p.available && (p.item || p.appName) && (p.status === 'playing' || p.status === 'paused' || p.status === 'buffering'))
    .sort((a, b) => Number(b.status === 'playing') - Number(a.status === 'playing') || a.name.localeCompare(b.name));
}
