import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import {
  Cast, Check, Headphones, Loader2, Speaker, Tv, Volume2, X,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { useOptionalWebPlayer } from '../../../context/webPlayerContext';
import { api } from '../../../services/api';
import { formatDeviceLabel } from '../../../lib/deviceLabels';
import { sendMaRemoteCommand } from '../../../lib/maRemotePlayer';
import { cn } from '../../../lib/utils';
import { haPlayerAdapter, maPlayerAdapter } from '../engine/adapters/adapters';
import type { MediaStatus } from '../types';

type Section = 'speaker' | 'tv' | 'group';

interface OutputRow {
  key: string;
  name: string;
  detail: string;
  online: boolean;
  playing: boolean;
  section: Section;
  /** The Music Assistant player id, when this output is an MA player. */
  maId: string | null;
  /** The Home Assistant entity id, when Home Assistant knows this output. */
  entityId: string | null;
  volume: number | null;
  /** Raw group children ids: HA entity ids or MA player ids. */
  groupMembers: string[];
}

interface RawMaPlayer {
  player_id?: unknown;
  name?: unknown;
  display_name?: unknown;
  available?: unknown;
  state?: unknown;
  volume_level?: unknown;
  volume_muted?: unknown;
  group_childs?: unknown;
  group_members?: unknown;
  /** Players Music Assistant can play in sync with this one. */
  can_group_with?: unknown;
  group_volume?: unknown;
  current_item?: { name?: unknown } | null;
  current_media?: { title?: unknown } | null;
}

const POLL_MS = 5000;

function maPlayersFrom(raw: unknown): RawMaPlayer[] {
  const result = raw && typeof raw === 'object' && 'result' in raw
    ? (raw as { result: unknown }).result
    : raw;
  if (!Array.isArray(result)) return [];
  return result.filter((entry): entry is RawMaPlayer => Boolean(entry && typeof entry === 'object'));
}

/** MA reports volume on either a 0..1 or a 0..100 scale. */
function normalizeVolume(value: unknown): number | null {
  const number = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.round(Math.min(100, Math.max(0, number <= 1 ? number * 100 : number)));
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function stateLabel(state: string | undefined, online: boolean): string {
  if (!online) return 'Offline';
  if (state === 'playing') return 'Playing';
  if (state === 'paused') return 'Paused';
  if (state === 'idle') return 'Ready';
  if (state === 'off') return 'Off';
  return 'Speaker';
}

function mediaTitle(raw: RawMaPlayer): string | undefined {
  const title = raw.current_media?.title ?? raw.current_item?.name;
  return typeof title === 'string' && title.trim() ? title : undefined;
}

/**
 * Where the music plays.
 *
 * The sheet is opened from the now-playing view. It lists the web player plus
 * every speaker Music Assistant or Home Assistant knows about, with the same
 * de-duplication the Media page uses: an MA player that also has an HA entity
 * appears once. Speakers that Music Assistant can move a queue to get a
 * "Play here" action; busy ones also get "Control", which opens the Media page
 * focused on that device.
 */
export default function OutputSheet({ open, onClose, onTransferred }: { open: boolean; onClose: () => void; onTransferred?: () => void }) {
  const player = useOptionalWebPlayer();
  const hasPlayer = Boolean(player);
  const navigate = useNavigate();
  const reduceMotion = useReducedMotion();

  const [statusPlayers, setStatusPlayers] = useState<MediaStatus[]>([]);
  const [maPlayers, setMaPlayers] = useState<RawMaPlayer[]>([]);
  const [loading, setLoading] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [groupBusy, setGroupBusy] = useState<string | null>(null);
  const [volumeOverrides, setVolumeOverrides] = useState<Record<string, number>>({});

  const volumeTimers = useRef(new Map<string, number>());
  useEffect(() => () => {
    for (const timer of volumeTimers.current.values()) window.clearTimeout(timer);
    volumeTimers.current.clear();
  }, []);

  const playerId = player?.playerId ?? null;
  // MA's own view of each player: who it is synced with and who it can sync with.
  const maById = useMemo(() => {
    const map = new Map<string, RawMaPlayer>();
    for (const entry of maPlayers) {
      if (typeof entry.player_id === 'string') map.set(entry.player_id, entry);
    }
    return map;
  }, [maPlayers]);
  const webPlaying = player?.isPlaying ?? false;
  const webTitle = player?.mediaTitle ?? null;
  const webVolume = player?.volume ?? 0;

  const refresh = useCallback(async () => {
    const [statusResult, maResult] = await Promise.allSettled([
      api.mediaStatus(),
      sendMaRemoteCommand('players/all'),
    ]);

    let statusOk = false;
    if (statusResult.status === 'fulfilled') {
      const response = statusResult.value;
      if (response.status === 'SUCCESS' && response.detail) {
        const detail = response.detail as { active?: MediaStatus | null; all_players?: MediaStatus[] };
        const active = detail.active;
        const all = (detail.all_players ?? [])
          .filter((entry) => entry.entity_id && entry.entity_id !== 'web_player');
        if (active?.entity_id && active.entity_id !== 'web_player' && !all.some((entry) => entry.entity_id === active.entity_id)) {
          all.push(active);
        }
        setStatusPlayers(all);
        statusOk = true;
      }
    }

    let maOk = false;
    if (maResult.status === 'fulfilled') {
      setMaPlayers(maPlayersFrom(maResult.value));
      maOk = true;
    }

    setUnavailable(!statusOk && !maOk);
  }, []);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      await refresh();
      if (!cancelled) setLoading(false);
    };
    void load();
    const id = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [open, refresh]);

  // Capture Escape before the now-playing sheet's own handler, so closing the
  // output picker never closes both layers at once.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      onClose();
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [open, onClose]);

  const rows = useMemo<OutputRow[]>(() => {
    const list: OutputRow[] = [];
    if (hasPlayer) {
      list.push({
        key: 'web',
        name: 'This device',
        detail: webPlaying ? 'Playing here' : webTitle ? 'Paused' : 'Web player',
        online: true,
        playing: webPlaying,
        section: 'speaker',
        maId: playerId,
        entityId: null,
        volume: webVolume,
        groupMembers: playerId ? stringList(maById.get(playerId)?.group_members) : [],
      });
    }

    const coveredMaIds = new Set<string>();
    for (const entry of statusPlayers) {
      const entityId = entry.entity_id ?? null;
      if (!entityId) continue;
      const maId = entry.ma_player_id || null;
      if (maId) coveredMaIds.add(maId);
      const maEntry = maId ? maById.get(maId) : undefined;
      const members = maEntry ? stringList(maEntry.group_members) : stringList(entry.group_members);
      const online = entry.state !== 'unavailable' && entry.state !== 'unknown';
      const title = entry.media_title?.trim() || undefined;
      const section: Section = members.length > 0 ? 'group' : entry.icon_kind === 'tv' ? 'tv' : 'speaker';
      list.push({
        key: maId ? `ma:${maId}` : entityId,
        name: formatDeviceLabel(entry.friendly_name, entityId),
        detail: title
          ? `${stateLabel(entry.state, online)} · ${title}`
          : stateLabel(entry.state, online),
        online,
        playing: entry.state === 'playing',
        section,
        maId,
        entityId,
        volume: entry.volume_level === null || entry.volume_level === undefined
          ? null
          : normalizeVolume(entry.volume_level),
        groupMembers: members,
      });
    }

    for (const entry of maPlayers) {
      const id = typeof entry.player_id === 'string' ? entry.player_id : '';
      if (!id || id === playerId || coveredMaIds.has(id)) continue;
      const online = entry.available !== false && entry.state !== 'unavailable';
      const title = mediaTitle(entry);
      const members = stringList(entry.group_childs ?? entry.group_members);
      list.push({
        key: `ma:${id}`,
        name: formatDeviceLabel(entry.name, entry.display_name, id),
        detail: title
          ? `${stateLabel(typeof entry.state === 'string' ? entry.state : undefined, online)} · ${title}`
          : stateLabel(typeof entry.state === 'string' ? entry.state : undefined, online),
        online,
        playing: entry.state === 'playing',
        section: members.length > 0 ? 'group' : 'speaker',
        maId: id,
        entityId: null,
        volume: normalizeVolume(entry.volume_level),
        groupMembers: members,
      });
    }

    return list;
  }, [hasPlayer, maById, maPlayers, playerId, statusPlayers, webPlaying, webTitle, webVolume]);

  const speakers = rows.filter((row) => row.key !== 'web' && row.section === 'speaker');
  const tvs = rows.filter((row) => row.section === 'tv');
  const groups = rows.filter((row) => row.section === 'group');

  const maIdByEntity = useMemo(() => {
    const map = new Map<string, string>();
    for (const row of rows) {
      if (row.entityId && row.maId) map.set(row.entityId, row.maId);
    }
    return map;
  }, [rows]);

  const othersIn = (row: OutputRow): string[] => row.groupMembers
    .map((member) => maIdByEntity.get(member) ?? member)
    .filter((member) => member && member !== row.maId);

  // Grouping is always relative to one Music Assistant player: one already
  // playing in sync with others (this device included), else this device when
  // it has something loaded, else a speaker that is playing. Without one there
  // is no queue to share, so no grouping controls are shown.
  const groupLeader = rows.find((row) => row.maId && othersIn(row).length > 0)
    ?? rows.find((row) => row.key === 'web' && (webPlaying || webTitle))
    ?? rows.find((row) => row.key !== 'web' && row.maId && row.playing)
    ?? null;

  const leaderMembers = useMemo(() => {
    if (!groupLeader?.maId) return new Set<string>();
    return new Set(
      groupLeader.groupMembers
        .map((member) => maIdByEntity.get(member) ?? member)
        .filter((member) => member && member !== groupLeader.maId),
    );
  }, [groupLeader, maIdByEntity]);

  const isGrouped = (row: OutputRow): boolean =>
    Boolean(row.maId && groupLeader?.maId && row.maId !== groupLeader.maId && leaderMembers.has(row.maId));

  // Only players Music Assistant can keep in sync with the leader may join it
  // (it reports them as can_group_with); current members can always leave.
  const syncable = useMemo(() => {
    const ids = new Set(leaderMembers);
    const leaderEntry = groupLeader?.maId ? maById.get(groupLeader.maId) : undefined;
    for (const id of stringList(leaderEntry?.can_group_with)) ids.add(id);
    return ids;
  }, [groupLeader, leaderMembers, maById]);

  const transfer = async (row: OutputRow) => {
    if (!player || !row.maId || !playerId) return;
    setBusyKey(row.key);
    try {
      await player.maCommand('player_queues/transfer', {
        source_queue_id: playerId,
        target_queue_id: row.maId,
      });
      // MA stops the source queue, but this browser's audio element can keep
      // playing until the event arrives. Pause it explicitly so the same song
      // never plays in two rooms.
      await player.pause().catch(() => undefined);
      toast.success(`Playing on ${row.name}`);
      onClose();
      onTransferred?.();
    } catch {
      toast.error(`Could not move playback to ${row.name}`);
    } finally {
      setBusyKey(null);
    }
  };

  const control = (row: OutputRow) => {
    onClose();
    const target = row.maId ? `ma:${row.maId}` : row.entityId;
    if (target) navigate(`/media?output=${encodeURIComponent(target)}`);
  };

  const changeVolume = (row: OutputRow, value: number) => {
    setVolumeOverrides((previous) => ({ ...previous, [row.key]: value }));
    const timers = volumeTimers.current;
    const existing = timers.get(row.key);
    if (existing !== undefined) window.clearTimeout(existing);
    timers.set(row.key, window.setTimeout(() => {
      timers.delete(row.key);
      const send = row.maId
        ? maPlayerAdapter.setVolume(`ma:${row.maId}`, value)
        : row.entityId
          ? haPlayerAdapter.setVolume(row.entityId, value)
          : Promise.resolve();
      void send.catch(() => toast.error(`Could not change the volume on ${row.name}`));
    }, 200));
  };

  /** Add players to, or take them out of, the leader's synced group. */
  const setMembers = async (busy: string, add: string[], remove: string[], done: string, failed: string) => {
    if (!groupLeader?.maId) return;
    setGroupBusy(busy);
    try {
      await sendMaRemoteCommand('players/cmd/set_members', {
        target_player: groupLeader.maId,
        player_ids_to_add: add,
        player_ids_to_remove: remove,
      });
      toast.success(done);
      await refresh();
    } catch {
      toast.error(failed);
    } finally {
      setGroupBusy(null);
    }
  };

  const toggleGroup = (row: OutputRow, checked: boolean) => {
    if (!groupLeader || !row.maId) return Promise.resolve();
    return checked
      ? setMembers(row.key, [row.maId], [], `${row.name} now plays in sync with ${groupLeader.name}`, 'Could not group that speaker')
      : setMembers(row.key, [], [row.maId], `Removed ${row.name} from the group`, 'Could not remove that speaker from the group');
  };

  const nameOf = (maId: string): string =>
    rows.find((row) => row.maId === maId)?.name
    ?? formatDeviceLabel(maById.get(maId)?.name, maById.get(maId)?.display_name, maId);

  const groupVolumeTimer = useRef<number | null>(null);
  useEffect(() => () => {
    if (groupVolumeTimer.current !== null) window.clearTimeout(groupVolumeTimer.current);
  }, []);
  const [groupVolume, setGroupVolume] = useState<number | null>(null);
  const leaderGroupVolume = groupLeader?.maId ? normalizeVolume(maById.get(groupLeader.maId)?.group_volume) : null;

  /** One volume for every speaker in the group, kept in proportion by MA. */
  const changeGroupVolume = (value: number) => {
    if (!groupLeader?.maId) return;
    const leaderId = groupLeader.maId;
    setGroupVolume(value);
    if (groupVolumeTimer.current !== null) window.clearTimeout(groupVolumeTimer.current);
    groupVolumeTimer.current = window.setTimeout(() => {
      groupVolumeTimer.current = null;
      void sendMaRemoteCommand('players/cmd/group_volume', { player_id: leaderId, volume_level: value })
        .catch(() => toast.error('Could not change the group volume'));
    }, 200);
  };

  /** The leader's members, each removable, and a way to break the group up. */
  const groupMembersPanel = () => {
    if (!groupLeader || leaderMembers.size === 0) return null;
    const members = [...leaderMembers];
    return (
      <div className="mt-2 space-y-1.5 pl-[52px]" data-testid="group-members">
        <p className="text-[11px] text-slate-400">Playing in sync with</p>
        <div className="flex items-center gap-2">
          <Volume2 size={14} className="shrink-0 text-slate-500" aria-hidden />
          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={groupVolume ?? leaderGroupVolume ?? 50}
            onChange={(event) => changeGroupVolume(Number(event.target.value))}
            aria-label="Group volume"
            className="h-8 w-full cursor-pointer accent-cyan-400"
          />
          <span className="w-8 shrink-0 text-right text-[11px] tabular-nums text-slate-500">{groupVolume ?? leaderGroupVolume ?? 50}</span>
        </div>
        {members.map((id) => (
          <div key={id} className="flex items-center gap-2 text-xs text-slate-200">
            <span className="min-w-0 flex-1 truncate">{nameOf(id)}</span>
            <button
              type="button"
              disabled={groupBusy !== null}
              onClick={() => void setMembers(`remove:${id}`, [], [id], `Removed ${nameOf(id)} from the group`, 'Could not remove that speaker from the group')}
              aria-label={`Remove ${nameOf(id)} from the group`}
              className="min-h-11 rounded-lg px-2.5 text-xs font-medium text-slate-300 hover:bg-white/10 hover:text-white disabled:opacity-40"
            >
              {groupBusy === `remove:${id}` ? <Loader2 size={13} className="animate-spin" aria-hidden /> : 'Remove'}
            </button>
          </div>
        ))}
        {members.length > 1 ? (
          <button
            type="button"
            disabled={groupBusy !== null}
            onClick={() => void setMembers('ungroup-all', [], members, 'Group ended', 'Could not end the group')}
            className="min-h-11 rounded-lg px-2.5 text-xs font-medium text-amber-200 hover:bg-white/10 disabled:opacity-40"
          >
            Ungroup all
          </button>
        ) : null}
      </div>
    );
  };

  const volumeFor = (row: OutputRow): number => volumeOverrides[row.key] ?? row.volume ?? 50;

  const rowVolumeVisible = (row: OutputRow): boolean =>
    row.online && row.volume !== null && (row.playing || isGrouped(row) || row.section === 'group');

  const renderRow = (row: OutputRow) => {
    const Icon = row.section === 'tv' ? Tv : row.section === 'group' ? Speaker : Speaker;
    const showGroup = Boolean(
      groupLeader && row.maId && row.key !== groupLeader.key && row.online && syncable.has(row.maId),
    );
    return (
      <div key={row.key} className={cn('rounded-xl border p-3', row.online ? 'border-white/10 bg-white/5' : 'border-white/5 bg-white/[0.02] opacity-60')}>
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/5 text-slate-300">
            <Icon size={19} aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-white">{row.name}</p>
            <p className="truncate text-xs text-slate-400">{row.detail}</p>
          </div>
          <span className={cn('h-2 w-2 shrink-0 rounded-full', row.online ? 'bg-emerald-400' : 'bg-slate-600')} aria-hidden />

          {row.online ? (
            <div className="flex shrink-0 items-center gap-1">
              {row.playing || !row.maId ? (
                <button
                  type="button"
                  onClick={() => control(row)}
                  aria-label={`Control ${row.name}`}
                  className="min-h-11 rounded-lg px-2.5 text-xs font-medium text-slate-300 hover:bg-white/10 hover:text-white"
                >
                  Control
                </button>
              ) : null}
              {row.maId ? (
                <button
                  type="button"
                  onClick={() => void transfer(row)}
                  disabled={busyKey !== null || groupBusy !== null || !playerId || !webTitle}
                  aria-label={`Play here on ${row.name}`}
                  className="flex min-h-11 items-center gap-1 rounded-lg bg-cyan-500/15 px-2.5 text-xs font-semibold text-cyan-200 hover:bg-cyan-500/25 disabled:opacity-40"
                >
                  {busyKey === row.key ? <Loader2 size={13} className="animate-spin" aria-hidden /> : <Cast size={13} aria-hidden />}
                  Play here
                </button>
              ) : null}
            </div>
          ) : (
            <span className="shrink-0 text-xs text-slate-500">Offline</span>
          )}
        </div>

        {rowVolumeVisible(row) ? (
          <div className="mt-2 flex items-center gap-2 pl-[52px]">
            <Volume2 size={14} className="shrink-0 text-slate-500" aria-hidden />
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={volumeFor(row)}
              onChange={(event) => changeVolume(row, Number(event.target.value))}
              aria-label={`Volume for ${row.name}`}
              className="h-8 w-full cursor-pointer accent-cyan-400"
            />
            <span className="w-8 shrink-0 text-right text-[11px] tabular-nums text-slate-500">{volumeFor(row)}</span>
          </div>
        ) : null}

        {groupLeader && row.key === groupLeader.key ? groupMembersPanel() : null}

        {showGroup ? (
          <label className="mt-2 flex min-h-11 cursor-pointer items-center gap-2 rounded-lg pl-[52px] text-xs text-slate-300">
            <input
              type="checkbox"
              checked={isGrouped(row)}
              disabled={groupBusy !== null}
              onChange={(event) => void toggleGroup(row, event.target.checked)}
              aria-label={`Group ${row.name} with ${groupLeader?.name}`}
              className="h-4 w-4 rounded border-white/20 bg-white/5 accent-cyan-400"
            />
            <span>Group with {groupLeader?.name}</span>
          </label>
        ) : null}
      </div>
    );
  };

  const sectionHeading = (label: string) => (
    <h3 className="mb-2 mt-4 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500 first:mt-0">{label}</h3>
  );

  return (
    <AnimatePresence>
      {open && player ? (
        <motion.div
          className="fixed inset-0 z-[70] flex items-end justify-center bg-black/60 sm:items-center sm:p-6"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: reduceMotion ? 0 : 0.18 }}
          onClick={onClose}
        >
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="Output"
            data-testid="output-sheet"
            onClick={(event) => event.stopPropagation()}
            initial={{ y: reduceMotion ? 0 : 48, opacity: reduceMotion ? 0 : 1 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: reduceMotion ? 0 : 48, opacity: 0 }}
            transition={reduceMotion ? { duration: 0 } : { type: 'spring', stiffness: 400, damping: 40 }}
            className="flex max-h-[88dvh] w-full max-w-lg flex-col overflow-hidden rounded-t-3xl border border-white/10 bg-slate-900/95 shadow-2xl shadow-black/60 backdrop-blur-xl safe-area-bottom sm:max-w-xl sm:rounded-3xl"
          >
            <header className="flex items-center justify-between border-b border-white/10 px-4 py-2">
              <div className="flex items-center gap-2">
                <Headphones size={17} className="text-cyan-300" aria-hidden />
                <h2 className="text-base font-semibold text-white">Output</h2>
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close output picker"
                className="flex min-h-11 min-w-11 items-center justify-center rounded-xl text-slate-400 hover:bg-white/10 hover:text-white"
              >
                <X size={19} />
              </button>
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
              {sectionHeading('This device')}
              {rows.filter((row) => row.key === 'web').map((row) => (
                <div key={row.key} className="flex items-center gap-3 rounded-xl border border-cyan-500/30 bg-cyan-500/10 p-3">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-cyan-500/15 text-cyan-200">
                    <Headphones size={19} aria-hidden />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-white">{row.name}</p>
                    <p className="truncate text-xs text-slate-300">{row.detail}</p>
                  </div>
                  <span className="flex items-center gap-1 text-xs font-medium text-cyan-200">
                    <Check size={14} aria-hidden />Active
                  </span>
                </div>
              ))}
              {groupLeader?.key === 'web' ? groupMembersPanel() : null}

              {loading && rows.length === 1 ? (
                <div className="flex items-center justify-center gap-2 py-6 text-sm text-slate-400" role="status">
                  <Loader2 size={16} className="animate-spin" aria-hidden />Looking for speakers…
                </div>
              ) : null}

              {unavailable ? (
                <p className="mt-3 rounded-xl border border-amber-400/20 bg-amber-400/5 px-3 py-2 text-xs text-amber-200">
                  Could not load the other speakers. Check that Music Assistant and Home Assistant are reachable.
                </p>
              ) : null}

              {!loading && !unavailable && speakers.length === 0 && tvs.length === 0 && groups.length === 0 ? (
                <p className="mt-3 text-xs text-slate-500">No other speakers found.</p>
              ) : null}

              {speakers.length > 0 ? (
                <>
                  {sectionHeading('Speakers')}
                  <div className="space-y-2">{speakers.map(renderRow)}</div>
                </>
              ) : null}

              {tvs.length > 0 ? (
                <>
                  {sectionHeading('TVs')}
                  <div className="space-y-2">{tvs.map(renderRow)}</div>
                </>
              ) : null}

              {groups.length > 0 ? (
                <>
                  {sectionHeading('Groups')}
                  <div className="space-y-2">{groups.map(renderRow)}</div>
                </>
              ) : null}

              {!groupLeader && speakers.length > 0 ? (
                <p className="mt-3 text-[11px] text-slate-500">
                  Play on a Music Assistant speaker to group other speakers with it.
                </p>
              ) : null}
              {groupLeader && syncable.size === 0 ? (
                <p className="mt-3 text-[11px] text-slate-500">
                  No other speaker can play in sync with {groupLeader.name}.
                </p>
              ) : groupLeader ? (
                <p className="mt-3 text-[11px] text-slate-500">
                  Only speakers that can play in sync with {groupLeader.name} can join its group.
                </p>
              ) : null}
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
