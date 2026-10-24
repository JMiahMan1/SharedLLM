/** What the Media page asks to play. Audiobookshelf items are resolved to a
 *  Music Assistant URI (or an ABS session, for Home Assistant speakers) at play
 *  time; everything else is already a Music Assistant URI. */
export type PlayRequest =
  | { kind: 'uri'; uri: string; title: string }
  | { kind: 'abs'; id: string; title: string };

/** Starts playback of `req`; `key` identifies the row that shows the spinner. */
export type PlayHandler = (req: PlayRequest, key: string) => Promise<void>;

/** Where a queued item goes: right after the current one, or at the end. */
export type QueueOption = 'next' | 'add';

/** Adds `req` to the selected device's queue without interrupting it. */
export type QueueHandler = (req: PlayRequest, option: QueueOption) => Promise<void>;

/** One entry in the device picker. `id` is '' for this device's web player,
 *  `ma:<player_id>` for a Music Assistant-only player, else an HA entity id. */
export interface DeviceOption {
  id: string;
  name: string;
  detail: string;
  online: boolean;
}

/** A player row from POST /execute/media/status (`detail.all_players`). */
export interface MediaStatus {
  entity_id?: string;
  friendly_name?: string;
  state?: string;
  media_title?: string;
  media_artist?: string;
  media_album?: string;
  volume_level?: number | null;
  is_volume_muted?: boolean;
  position?: number;
  duration?: number;
  entity_picture?: string;
  media_content_id?: string;
  media_type?: string;
  ma_player_id?: string | null;
  icon_kind?: string | null;
  app_name?: string | null;
  /** Players grouped with this one (HA entity ids or MA player ids). */
  group_members?: string[];
  /** When `position` was read (ISO), so the clock can run on from it. */
  media_position_updated_at?: string | null;
  shuffle?: boolean | null;
  repeat?: string | null;
}

/** Shared row styling: full-width, at least 44 px tall for touch. */
export const ROW_CLASS =
  'w-full min-h-11 flex items-center gap-3 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left disabled:opacity-50 group';
