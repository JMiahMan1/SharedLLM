import { create } from 'zustand';
import { createJSONStorage, persist, type StateStorage } from 'zustand/middleware';
import type { MediaStatus } from '../../../components/media/types';
import type { OutputId, RemotePlayer } from '../types';
import { THIS_DEVICE } from '../types';
import {
  EMPTY_PLAYERS, ingest, touchMute, touchVolume, type MediaEvent, type PlayersState,
} from './ingest';

/**
 * The media store (§6.3): which output the controls drive, what every remote
 * output is doing, and the media overlays' open state. Components read it
 * with selectors; the only writers are `ingest` (status poll, Music Assistant
 * player list, event stream) and the actions below.
 *
 * This device's own playback is not mirrored here: it lives in the web player
 * hook (`useMAWebPlayer`, shared by `MediaPlayerProvider`), which is its one
 * source of truth.
 */

export type SheetName = 'nowPlaying' | 'queue' | 'output';

export const MAX_RECENT_SEARCHES = 10;

export interface MediaStoreState extends PlayersState {
  activeOutputId: OutputId;
  /** Once the person picks an output the app stops following live speakers. */
  userPickedOutput: boolean;
  /** Actions in flight, keyed like `play:<uri>` or `transport:next`. */
  pending: Record<string, true>;
  sheets: Record<SheetName, boolean>;
  /** The server's media event stream. */
  events: 'connecting' | 'open' | 'closed';
  recentSearches: string[];
  actions: {
    selectOutput: (id: OutputId, opts?: { picked?: boolean }) => void;
    /** Fold the status poll in; follow a playing speaker until the person picks one. */
    ingestStatus: (players: MediaStatus[], active?: MediaStatus | null) => void;
    ingest: (event: MediaEvent) => void;
    setVolume: (id: OutputId, volume: number) => void;
    setMuted: (id: OutputId, muted: boolean) => void;
    /** Show a command's expected result before the device confirms it. */
    patchPlayer: (id: OutputId, patch: Partial<Pick<RemotePlayer, 'status' | 'positionSec' | 'positionUpdatedAt' | 'shuffle' | 'repeat'>>) => void;
    setPending: (key: string, on: boolean) => void;
    openSheet: (name: SheetName) => void;
    closeSheet: (name: SheetName) => void;
    setEvents: (state: MediaStoreState['events']) => void;
    addRecentSearch: (q: string) => void;
    clearRecentSearches: () => void;
  };
}

/** localStorage that never throws (private mode, blocked storage). */
const safeStorage: StateStorage = {
  getItem: (name) => {
    try { return localStorage.getItem(name); } catch { return null; }
  },
  setItem: (name, value) => {
    try { localStorage.setItem(name, value); } catch { /* not persisted */ }
  },
  removeItem: (name) => {
    try { localStorage.removeItem(name); } catch { /* nothing stored */ }
  },
};

const INITIAL = {
  ...EMPTY_PLAYERS,
  activeOutputId: THIS_DEVICE,
  userPickedOutput: false,
  pending: {},
  sheets: { nowPlaying: false, queue: false, output: false },
  events: 'closed' as const,
  recentSearches: [] as string[],
};

export const useMediaStore = create<MediaStoreState>()(
  persist(
    (set, get) => ({
      ...INITIAL,
      actions: {
        selectOutput: (id, opts = {}) => set({ activeOutputId: id, userPickedOutput: opts.picked ?? true }),
        ingestStatus: (players, active) => {
          const now = Date.now();
          const rows = active?.entity_id
            ? [...players.map((p) => (p.entity_id === active.entity_id ? { ...p, ...active } : p)),
               ...(players.some((p) => p.entity_id === active.entity_id) ? [] : [active])]
            : players;
          const s = get();
          const next = ingest(s, { type: 'status', players: rows }, now);
          const follow = !s.userPickedOutput && s.activeOutputId === THIS_DEVICE
            && active?.entity_id && active.entity_id !== 'web_player' && active.state === 'playing';
          if (follow) set({ players: next.players, activeOutputId: active!.entity_id!, userPickedOutput: true });
          else if (next !== s) set({ players: next.players });
        },
        ingest: (event) => {
          const s = get();
          const next = ingest(s, event, Date.now());
          if (next !== s) set({ players: next.players, volumeTouchedAt: next.volumeTouchedAt });
        },
        setVolume: (id, volume) => {
          const next = touchVolume(get(), id, volume, Date.now());
          set({ players: next.players, volumeTouchedAt: next.volumeTouchedAt });
        },
        setMuted: (id, muted) => {
          const next = touchMute(get(), id, muted, Date.now());
          set({ players: next.players, volumeTouchedAt: next.volumeTouchedAt });
        },
        patchPlayer: (id, patch) => {
          const prev = get().players[id];
          if (prev) set({ players: { ...get().players, [id]: { ...prev, ...patch } } });
        },
        setPending: (key, on) => {
          const pending = { ...get().pending };
          if (on) pending[key] = true;
          else delete pending[key];
          set({ pending });
        },
        openSheet: (name) => set({ sheets: { ...get().sheets, [name]: true } }),
        closeSheet: (name) => set({ sheets: { ...get().sheets, [name]: false } }),
        setEvents: (events) => set({ events }),
        addRecentSearch: (q) => {
          const query = q.trim();
          if (!query) return;
          const rest = get().recentSearches.filter((r) => r.toLowerCase() !== query.toLowerCase());
          set({ recentSearches: [query, ...rest].slice(0, MAX_RECENT_SEARCHES) });
        },
        clearRecentSearches: () => set({ recentSearches: [] }),
      },
    }),
    {
      name: 'jarvis_media',
      version: 1,
      storage: createJSONStorage(() => safeStorage),
      // Only the choices worth keeping across a restart (§6.3).
      partialize: (s) => ({
        activeOutputId: s.activeOutputId,
        userPickedOutput: s.userPickedOutput,
        recentSearches: s.recentSearches,
      }),
    },
  ),
);

export const useMediaActions = () => useMediaStore((s) => s.actions);

/** Tests only: back to a fresh store. */
export function resetMediaStore(): void {
  useMediaStore.setState({ ...INITIAL, players: {}, volumeTouchedAt: {}, pending: {}, sheets: { ...INITIAL.sheets }, recentSearches: [] });
}
