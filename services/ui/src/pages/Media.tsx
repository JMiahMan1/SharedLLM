import { useState, useCallback, useEffect, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Play, Pause, Volume2, Volume1, VolumeX, Cast,
  Music, BookOpen, List, Loader2, X, Library, Search,
  SkipBack as SkipBackIcon, SkipForward as SkipForwardIcon,
  ChevronRight, Grid3X3, Clock, Headphones,
} from 'lucide-react';
import { api } from '../services/api';
import { useHaptics } from '../hooks/useHaptics';
import { LocalAudioPlayer } from '../components/LocalAudioPlayer';

interface MediaStatus {
  entity_id?: string;
  friendly_name?: string;
  state?: string;
  media_title?: string;
  media_artist?: string;
  media_album?: string;
  volume_level?: number;
  is_volume_muted?: boolean;
}

interface MediaEntity {
  entity_id: string;
  friendly_name: string;
  state: string;
  domain: string;
}

/* ── helpers ─────────────────────────────────────────────────────────── */

const emptySection = (msg: string) => (
  <div className="glass-panel rounded-2xl p-8 text-center text-slate-500 text-sm">
    {msg}
  </div>
);

const loadingSection = () => (
  <div className="flex items-center justify-center py-8">
    <Loader2 size={24} className="text-slate-500 animate-spin" />
  </div>
);

/* ── device selector (horizontal card list) ────────────────────────── */



const DeviceSelector = ({
  selectedTarget,
  entities,
  onDeviceSelect,
  localMode,
  onLocalToggle,
}: {
  selectedTarget: string;
  entities: MediaEntity[];
  onDeviceSelect?: (entityId: string) => void;
  localMode: boolean;
  onLocalToggle?: (mode: boolean) => void;
}) => {
  const { trigger } = useHaptics();

  const targets = useMemo(
    () =>
      entities.map((e) => ({
        id: e.entity_id,
        name: e.friendly_name || e.entity_id,
        room: e.entity_id.split('.')[1]?.replace(/_/g, ' ') || 'Unknown',
        online: e.state !== 'unavailable' && e.state !== 'unknown',
      })),
    [entities],
  );

  const handleLocalSelect = useCallback(() => {
    trigger('light');
    onLocalToggle?.(true);
  }, [onLocalToggle, trigger]);

  const handleDeviceSelect = useCallback(
    (id: string) => {
      trigger('light');
      onLocalToggle?.(false);
      onDeviceSelect?.(id);
    },
    [onDeviceSelect, onLocalToggle, trigger],
  );

  const hasHaDevices = targets.length > 0;

  return (
    <div className="glass-panel rounded-2xl p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
          <Cast size={12} />Select Device
        </h2>
        <span className="text-[10px] text-slate-600">{localMode ? '1 mode' : `${targets.filter((t) => t.online).length} online`}</span>
      </div>
      <div className="flex gap-2 overflow-x-auto custom-scrollbar pb-1">
        {/* Local Player */}
        <button
          onClick={handleLocalSelect}
          className={`flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl border shrink-0 transition-all text-left min-w-[160px] ${
            localMode
              ? 'bg-cyan-500/15 border-cyan-500/40 shadow-lg shadow-cyan-500/5'
              : 'bg-white/5 border-white/10 hover:bg-white/10 hover:border-white/20'
          }`}
        >
          <div className="w-2.5 h-2.5 rounded-full shrink-0 bg-green-400" />
          <div className="min-w-0 flex-1">
            <p className="text-white text-sm font-medium truncate">Local Player</p>
            <p className="text-[10px] text-slate-500 truncate">Browser / Android App</p>
          </div>
          {localMode && (
            <div className="w-1.5 h-1.5 rounded-full bg-cyan-400 shrink-0 shadow-sm shadow-cyan-400/50" />
          )}
        </button>
        {/* HA Devices */}
        {hasHaDevices && (
          <>
            {targets.map((t) => (
              <button
                key={t.id}
                onClick={() => handleDeviceSelect(t.id)}
                className={`flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl border shrink-0 transition-all text-left min-w-[160px] ${
                  selectedTarget === t.id
                    ? 'bg-cyan-500/15 border-cyan-500/40 shadow-lg shadow-cyan-500/5'
                    : t.online
                      ? 'bg-white/5 border-white/10 hover:bg-white/10 hover:border-white/20'
                      : 'bg-white/3 border-white/5 opacity-40'
                }`}
              >
                <div className={`w-2.5 h-2.5 rounded-full shrink-0 ${t.online ? 'bg-green-400' : 'bg-slate-600'}`} />
                <div className="min-w-0 flex-1">
                  <p className="text-white text-sm font-medium truncate">{t.name}</p>
                  <p className="text-[10px] text-slate-500 truncate">{t.room}</p>
                </div>
                {selectedTarget === t.id && (
                  <div className="w-1.5 h-1.5 rounded-full bg-cyan-400 shrink-0 shadow-sm shadow-cyan-400/50" />
                )}
              </button>
            ))}
          </>
        )}
        {!localMode && !hasHaDevices && (
          <div className="flex items-center justify-center px-4 py-2.5 rounded-xl border border-dashed border-white/5 text-slate-600 text-xs shrink-0">
            Tap a device to start
          </div>
        )}
      </div>
    </div>
  );
};

/* ── player header ──────────────────────────────────────────────────── */

const NowPlayingCard = ({
  mediaStatus,
  selectedTarget,
  volume,
  muted,
  loading,
  onPrevious,
  onTogglePlay,
  onNext,
  onVolumeChange,
  onMuteToggle,
}: {
  mediaStatus: MediaStatus | null;
  selectedTarget: string;
  volume: number;
  muted: boolean;
  loading: string | null;
  onPrevious: () => void;
  onTogglePlay: () => void;
  onNext: () => void;
  onVolumeChange: (v: number) => void;
  onMuteToggle: () => void;
}) => {
  const nowPlaying = mediaStatus?.state === 'playing' || mediaStatus?.state === 'paused';

  return (
    <div className="glass-panel rounded-2xl p-5 border border-cyan-500/20 relative overflow-visible">
      <div className="absolute inset-0 bg-gradient-to-br from-cyan-500/5 via-transparent to-purple-500/5 pointer-events-none rounded-2xl" />

      <div className="relative flex flex-col sm:flex-row sm:items-center gap-4">
        {/* icon */}
        <div className="w-16 h-16 sm:w-20 sm:h-20 rounded-xl bg-gradient-to-br from-cyan-500/30 to-purple-500/30 flex items-center justify-center shrink-0 shadow-lg shadow-cyan-500/10">
          {nowPlaying ? <Music size={28} className="text-cyan-400" /> : <Play size={28} className="text-cyan-400" />}
        </div>

        {/* metadata + transport */}
        <div className="flex-1 min-w-0">
          {nowPlaying ? (
            <>
              <p className="text-white font-medium text-lg truncate">{mediaStatus.media_title || 'Unknown Title'}</p>
              <p className="text-sm text-slate-400 truncate">{mediaStatus.media_artist || 'Unknown Artist'}</p>
            </>
          ) : (
            <>
              <p className="text-white font-medium text-lg">No Active Playback</p>
              <p className="text-sm text-slate-400">Select a device and content to begin</p>
            </>
          )}

          <div className="flex items-center justify-center gap-4 mt-3">
            <button onClick={onPrevious} disabled={loading !== null}
              className="text-slate-400 hover:text-white transition-colors p-2 disabled:opacity-50 rounded-lg hover:bg-white/5" aria-label="Previous track">
              {loading === 'previous' ? <Loader2 size={20} className="animate-spin" /> : <SkipBackIcon size={20} />}
            </button>
            <button onClick={onTogglePlay} disabled={loading !== null}
              className="w-12 h-12 rounded-full bg-cyan-500/20 border border-cyan-500/30 flex items-center justify-center text-cyan-400 hover:bg-cyan-500/30 hover:scale-105 transition-all disabled:opacity-50"
              aria-label={mediaStatus?.state === 'playing' ? 'Pause' : 'Play'}>
              {loading === 'play' || loading === 'pause' ? <Loader2 size={20} className="animate-spin" /> :
                mediaStatus?.state === 'playing' ? <Pause size={22} /> : <Play size={22} />}
            </button>
            <button onClick={onNext} disabled={loading !== null}
              className="text-slate-400 hover:text-white transition-colors p-2 disabled:opacity-50 rounded-lg hover:bg-white/5" aria-label="Next track">
              {loading === 'next' ? <Loader2 size={20} className="animate-spin" /> : <SkipForwardIcon size={20} />}
            </button>
          </div>
        </div>

        {/* volume control */}
        <div className="flex sm:flex-col items-center sm:items-end gap-3 shrink-0">
          <div className="flex items-center gap-2">
            <button onClick={onMuteToggle}
              className="text-slate-400 hover:text-white transition-colors p-1.5 rounded-lg hover:bg-white/5"
              aria-label={muted ? 'Unmute' : 'Mute'}>
              {muted || volume === 0 ? <VolumeX size={16} /> : volume < 50 ? <Volume1 size={16} /> : <Volume2 size={16} />}
            </button>
            <input type="range" min="0" max="100" value={muted ? 0 : volume}
              onChange={(e) => onVolumeChange(Number(e.target.value))}
              className="w-20 sm:w-24 accent-cyan-400" aria-label="Volume" />
            <span className="text-xs text-slate-500 w-8 text-right tabular-nums">{muted ? 'M' : `${volume}`}</span>
          </div>
        </div>
      </div>

      {!selectedTarget && (
        <div className="relative mt-4 pt-3 border-t border-white/5">
          <div className="flex items-center justify-center gap-2 py-2 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-400 text-sm">
            <Cast size={14} />
            Select a device above to enable playback
          </div>
        </div>
      )}
    </div>
  );
};

/* ── resume / playlist item cards ───────────────────────────────────── */

const QuickResumeItem = ({
  item, onPlay, isDisabled, isLoading,
}: {
  item: { id: string; title: string; subtitle: string; type: 'audiobook' | 'music'; progress?: number };
  onPlay: () => void;
  isDisabled: boolean;
  isLoading: boolean;
}) => (
  <button
    onClick={onPlay} disabled={isDisabled}
    className="glass-panel rounded-xl p-3 flex items-center gap-3 text-left transition-all hover:bg-white/10 hover:scale-[1.01] disabled:opacity-50 disabled:cursor-not-allowed group"
  >
    <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
      item.type === 'audiobook' ? 'bg-amber-500/20 text-amber-400' : 'bg-purple-500/20 text-purple-400'
    }`}>
      {isLoading ? <Loader2 size={18} className="animate-spin" /> :
        item.type === 'audiobook' ? <BookOpen size={18} /> : <Music size={18} />}
    </div>
    <div className="min-w-0 flex-1">
      <p className="text-white text-sm font-medium truncate">{item.title}</p>
      <p className="text-xs text-slate-400 truncate">{item.subtitle}</p>
      {item.type === 'audiobook' && item.progress !== undefined && (
        <div className="flex items-center gap-2 mt-1.5">
          <div className="flex-1 h-1 bg-white/10 rounded-full overflow-hidden">
            <div className="h-full bg-amber-400 rounded-full transition-all"
              style={{ width: `${Math.min(100, Math.round(item.progress * 100))}%` }} />
          </div>
          <span className="text-[10px] text-slate-500 shrink-0 tabular-nums">{Math.min(100, Math.round(item.progress * 100))}%</span>
        </div>
      )}
    </div>
    <Play size={16} className="text-slate-500 opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
  </button>
);

const PlaylistItem = ({
  name, trackCount, onPlay, isDisabled, isLoading,
}: {
  name: string; trackCount: number; onPlay: () => void; isDisabled: boolean; isLoading: boolean;
}) => (
  <button
    onClick={onPlay} disabled={isDisabled}
    className="glass-panel rounded-xl p-3 flex items-center gap-3 text-left transition-all hover:bg-white/10 hover:scale-[1.01] disabled:opacity-50 disabled:cursor-not-allowed group"
  >
    <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-cyan-500/20 to-purple-500/20 text-cyan-400 flex items-center justify-center shrink-0">
      {isLoading ? <Loader2 size={18} className="animate-spin" /> : <List size={18} />}
    </div>
    <div className="min-w-0 flex-1">
      <p className="text-white text-sm font-medium truncate">{name}</p>
      {trackCount > 0 && (
        <p className="text-xs text-slate-400">{trackCount} {trackCount === 1 ? 'track' : 'tracks'}</p>
      )}
    </div>
    <Play size={16} className="text-slate-500 opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
  </button>
);

/* ── explorer modal ─────────────────────────────────────────────────── */

const MediaExplorerModal = ({
  show, onClose, playAudiobook, playPlaylist, playMedia, playLocal, selectedTarget, localMode,
}: {
  show: boolean; onClose: () => void;
  playAudiobook: (id: string) => void;
  playPlaylist: (uri: string) => void;
  playMedia: (query: string, mediaType?: string) => void;
  playLocal: (id: string, title: string, subtitle: string, type: 'audiobook' | 'music', source: 'abs' | 'ma') => void;
  selectedTarget: string;
  localMode: boolean;
}) => {
  const { trigger } = useHaptics();
  const [tab, setTab] = useState<'ma' | 'abs'>('ma');
  const [search, setSearch] = useState('');
  const [libraryId, setLibraryId] = useState<string | null>(null);
  const [itemLoading, setItemLoading] = useState<string | null>(null);

  const { data: absLibraries, isLoading: absLibrariesLoading, error: absLibrariesError } = useQuery({
    queryKey: ['abs-libraries'],
    queryFn: () => api.getAudiobookshelfLibraries(),
    enabled: show && tab === 'abs' && !libraryId,
    retry: 2,
    staleTime: 60000,
  });

  const { data: absLibraryItems, isLoading: absLibraryItemsLoading, error: absLibraryItemsError } = useQuery({
    queryKey: ['abs-library-items', libraryId],
    queryFn: () => api.getAudiobookshelfLibrary(libraryId!, 50),
    enabled: show && tab === 'abs' && !!libraryId,
    retry: 2,
    staleTime: 60000,
  });

  const { data: absSearchResults, isLoading: absSearchLoading, error: absSearchError } = useQuery({
    queryKey: ['abs-search', search],
    queryFn: () => api.searchAudiobookshelf(search, 30),
    enabled: show && tab === 'abs' && search.length >= 2,
    retry: 2,
    staleTime: 60000,
  });

  const { data: maPlaylists, isLoading: playlistsLoading, error: maPlaylistsError } = useQuery({
    queryKey: ['ma-playlists'],
    queryFn: () => api.getMusicAssistantPlaylists(),
    enabled: show && tab === 'ma',
    retry: 2,
    staleTime: 60000,
  });

  const { data: maRecent, isLoading: maRecentLoading, error: maRecentError } = useQuery({
    queryKey: ['ma-recent'],
    queryFn: () => api.getMusicAssistantRecent(),
    enabled: show && tab === 'ma',
    retry: 2,
    staleTime: 60000,
  });

  const handlePlay = useCallback(
    (id: string, type: 'audiobook' | 'music' | 'playlist', title?: string, subtitle?: string) => {
      if (localMode) {
        if (title && subtitle) {
          trigger('heavy');
          setItemLoading(id);
          try {
            const source = type === 'audiobook' ? 'abs' : 'ma';
            playLocal(id, title, subtitle, type === 'audiobook' ? 'audiobook' : 'music', source);
          } finally { setItemLoading(null); }
        }
        return;
      }
      if (!selectedTarget) {
        if (title && subtitle) {
          trigger('heavy');
          setItemLoading(id);
          try {
            const source = type === 'audiobook' ? 'abs' : 'ma';
            playLocal(id, title, subtitle, type === 'audiobook' ? 'audiobook' : 'music', source);
          } finally { setItemLoading(null); }
        }
        return;
      }
      trigger('heavy');
      setItemLoading(id);
      try {
        if (type === 'audiobook') playAudiobook(id);
        else if (type === 'playlist') playPlaylist(id);
        else playMedia(id, 'music');
      } finally { setItemLoading(null); }
    },
    [selectedTarget, localMode, trigger, playAudiobook, playPlaylist, playMedia, playLocal],
  );

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!show) { setSearch(''); setLibraryId(null); setTab('ma'); setItemLoading(null); }
  }, [show]);
  /* eslint-enable react-hooks/set-state-in-effect */

  if (!show) return null;

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-end sm:items-center justify-center" onClick={onClose}>
      <div className="glass-panel w-full sm:max-w-3xl h-[92vh] sm:h-[85vh] sm:rounded-2xl rounded-t-2xl flex flex-col overflow-hidden border border-white/10 shadow-2xl"
        onClick={(e) => e.stopPropagation()}>

        {/* header */}
        <div className="flex items-center justify-between p-4 border-b border-white/10 shrink-0">
          <div className="flex items-center gap-2">
            <Grid3X3 size={18} className="text-cyan-400" />
            <h2 className="text-lg font-semibold text-white">Browse All Media</h2>
          </div>
          <button onClick={onClose}
            className="text-slate-400 hover:text-white p-1.5 rounded-lg hover:bg-white/10 transition-colors" aria-label="Close">
            <X size={20} />
          </button>
        </div>

        {/* tabs */}
        <div className="flex border-b border-white/10 shrink-0">
          <button onClick={() => { setTab('ma'); setLibraryId(null); setSearch(''); }}
            className={`flex-1 py-3 text-sm font-medium flex items-center justify-center gap-2 transition-all ${
              tab === 'ma' ? 'text-cyan-400 border-b-2 border-cyan-400 bg-cyan-500/5' : 'text-slate-400 hover:text-white'}`}>
            <Music size={16} />Music Assistant
          </button>
          <button onClick={() => { setTab('abs'); setLibraryId(null); setSearch(''); }}
            className={`flex-1 py-3 text-sm font-medium flex items-center justify-center gap-2 transition-all ${
              tab === 'abs' ? 'text-amber-400 border-b-2 border-amber-400 bg-amber-500/5' : 'text-slate-400 hover:text-white'}`}>
            <BookOpen size={16} />Audiobooks
          </button>
        </div>

        {/* sticky search */}
        <div className="p-3 border-b border-white/5 shrink-0 bg-slate-950/30">
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input type="text"
              placeholder={tab === 'abs' ? 'Search audiobooks...' : 'Filter playlists and recent...'}
              value={search} onChange={(e) => setSearch(e.target.value)}
              className="w-full glass-input pl-9 h-10 text-sm rounded-xl" />
            {search && (
              <button onClick={() => setSearch('')}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white p-0.5" aria-label="Clear">
                <X size={14} />
              </button>
            )}
          </div>
        </div>

        {/* content */}
        <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
          {tab === 'ma' && (
            <div className="space-y-6">
              <div>
                <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                  <List size={12} />Playlists
                </h3>
                {playlistsLoading ? loadingSection() : maPlaylistsError ? (
                  <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-center">
                    <p className="text-sm text-red-400">Failed to load playlists. Check your server connection.</p>
                    <button
                      onClick={() => { /* query will auto-retry via staleTime */ }}
                      className="mt-2 text-xs text-red-300 underline hover:text-red-200"
                    >
                      Retry
                    </button>
                  </div>
                ) : !maPlaylists?.playlists?.length ? emptySection('No playlists found') : (
                  <div className="space-y-1.5">
                    {maPlaylists.playlists
                      .filter((pl) => !search || pl.name.toLowerCase().includes(search.toLowerCase()))
                      .map((pl) => (
                        <PlaylistItem key={pl.uri} name={pl.name} trackCount={pl.items}
                          onPlay={() => handlePlay(pl.uri, 'playlist', pl.name, pl.items > 0 ? `${pl.items} tracks` : 'Playlist')}
                          isDisabled={!selectedTarget} isLoading={itemLoading === `pl-${pl.uri}`} />
                      ))}
                  </div>
                )}
              </div>
              <div>
                <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                  <Clock size={12} />Recently Played
                </h3>
                {maRecentLoading ? loadingSection() : maRecentError ? (
                  <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-center">
                    <p className="text-sm text-red-400">Failed to load recent items. Check your server connection.</p>
                    <button
                      onClick={() => { /* query will auto-retry via staleTime */ }}
                      className="mt-2 text-xs text-red-300 underline hover:text-red-200"
                    >
                      Retry
                    </button>
                  </div>
                ) : !maRecent?.recent?.length ? emptySection('No recent items') : (
                  <div className="space-y-1.5">
                    {maRecent.recent
                      .filter((i) => !search || i.name.toLowerCase().includes(search.toLowerCase()) || i.artist.toLowerCase().includes(search.toLowerCase()))
                      .map((item) => (
                        <button key={item.uri} onClick={() => handlePlay(item.uri, 'music', item.name, item.artist)} disabled={!selectedTarget}
                          className="w-full flex items-center gap-3 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left disabled:opacity-50 group">
                          {itemLoading === `ma-${item.uri}` ?
                            <Loader2 size={18} className="text-purple-400 animate-spin shrink-0" /> :
                            <Music size={18} className="text-purple-400 shrink-0" />}
                          <div className="min-w-0 flex-1">
                            <p className="text-white text-sm font-medium truncate">{item.name}</p>
                            <p className="text-xs text-slate-400 truncate">{item.artist}</p>
                          </div>
                          <Play size={16} className="text-slate-500 opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
                        </button>
                      ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {tab === 'abs' && (
            <div>
              {libraryId && absLibraries && (
                <button onClick={() => { setLibraryId(null); setSearch(''); }}
                  className="text-sm text-amber-400 hover:text-amber-300 mb-3 flex items-center gap-1 transition-colors">
                  <ChevronRight size={14} className="rotate-180" />Back to Libraries
                </button>
              )}

              {!libraryId && (
                <div>
                  <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                    <Library size={12} />Libraries
                  </h3>
                  {absLibrariesLoading ? loadingSection() : absLibrariesError ? (
                    <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-center">
                      <p className="text-sm text-red-400">Failed to load libraries. Check your server connection.</p>
                    </div>
                  ) : !absLibraries?.libraries?.length ? emptySection('No libraries found') : (
                    <div className="space-y-1.5">
                      {absLibraries.libraries
                        .filter((lib) => !search || lib.name.toLowerCase().includes(search.toLowerCase()))
                        .map((lib) => (
                          <button key={lib.id} onClick={() => setLibraryId(lib.id)}
                            className="w-full flex items-center gap-3 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left">
                            <Library size={18} className="text-amber-400 shrink-0" />
                            <div className="min-w-0 flex-1">
                              <p className="text-white text-sm font-medium truncate">{lib.name}</p>
                              <p className="text-xs text-slate-400 capitalize">{lib.media_type}</p>
                            </div>
                            <ChevronRight size={16} className="text-slate-500 shrink-0" />
                          </button>
                        ))}
                    </div>
                  )}
                </div>
              )}

              {libraryId && (
                <div>
                  <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
                    {absLibraryItems?.status ? 'Browse Books' : 'Loading...'}
                  </h3>
                  {absLibraryItemsLoading ? loadingSection() : absLibraryItemsError ? (
                    <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-center">
                      <p className="text-sm text-red-400">Failed to load library. Check your server connection.</p>
                    </div>
                  ) : !absLibraryItems?.books?.length ? emptySection('No books in this library') : (
                    <div className="space-y-1.5">
                      {absLibraryItems.books
                        .filter((b) => !search || b.title.toLowerCase().includes(search.toLowerCase()) || b.author.toLowerCase().includes(search.toLowerCase()))
                        .map((book) => (
<button key={book.id} onClick={() => handlePlay(book.id, 'audiobook', book.title, book.author)} disabled={!selectedTarget}
                            className="w-full flex items-center gap-3 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left disabled:opacity-50 group">
                            {itemLoading === `abs-${book.id}` ?
                              <Loader2 size={18} className="text-amber-400 animate-spin shrink-0" /> :
                              <BookOpen size={18} className="text-amber-400 shrink-0" />}
                            <div className="min-w-0 flex-1">
                              <p className="text-white text-sm font-medium truncate">{book.title}</p>
                              <p className="text-xs text-slate-400">{book.author}</p>
                            </div>
                            <Play size={16} className="text-slate-500 opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
                          </button>
                        ))}
                    </div>
                  )}
                </div>
              )}

              {search.length >= 2 && (
                <div className="mt-4">
                  <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                    <Search size={12} />Search Results
                  </h3>
                  {absSearchLoading ? loadingSection() : absSearchError ? (
                    <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-center">
                      <p className="text-sm text-red-400">Search failed. Check your server connection.</p>
                    </div>
                  ) : !absSearchResults?.books?.length ? emptySection(`No results for "${search}"`) : (
                    <div className="space-y-1.5">
                      {absSearchResults.books.map((book) => (
                        <button key={book.id} onClick={() => handlePlay(book.id, 'audiobook', book.title, book.author)} disabled={!selectedTarget}
                          className="w-full flex items-center gap-3 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left disabled:opacity-50 group">
                          {itemLoading === `abs-${book.id}` ?
                            <Loader2 size={18} className="text-amber-400 animate-spin shrink-0" /> :
                            <BookOpen size={18} className="text-amber-400 shrink-0" />}
                          <div className="min-w-0 flex-1">
                            <p className="text-white text-sm font-medium truncate">{book.title}</p>
                            <p className="text-xs text-slate-400">{book.author}</p>
                          </div>
                          {book.narrator && <Headphones size={12} className="text-slate-500 shrink-0" />}
                          <Play size={16} className="text-slate-500 opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

/* ── main page ──────────────────────────────────────────────────────── */

const Media = () => {
  const { trigger } = useHaptics();
  const queryClient = useQueryClient();
  const [selectedTarget, setSelectedTarget] = useState<string>('');
  const [volume, setVolume] = useState(70);
  const [muted, setMuted] = useState(false);
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mediaStatus, setMediaStatus] = useState<MediaStatus | null>(null);
  const [showMediaPicker, setShowMediaPicker] = useState(false);
  const [localTrack, setLocalTrack] = useState<{ id: string; title: string; subtitle: string; type: 'audiobook' | 'music'; source: 'abs' | 'ma' } | null>(null);
  const [localMode, setLocalMode] = useState(false);

  const { data: maPlaylists, isLoading: maPlaylistsLoading } = useQuery({
    queryKey: ['ma-playlists'],
    queryFn: () => api.getMusicAssistantPlaylists(),
  });
  const { data: maRecent, isLoading: maRecentLoading } = useQuery({
    queryKey: ['ma-recent'],
    queryFn: () => api.getMusicAssistantRecent(),
  });
  const { data: absLastPlayed, isLoading: absLoading } = useQuery({
    queryKey: ['abs-last-played'],
    queryFn: () => api.getAudiobookshelfLastPlayed(),
  });

  const { data: entities = [] } = useQuery({
    queryKey: ['media-entities'],
    queryFn: () => api.getEntities(),
    select: (data: MediaEntity[]) => data.filter((e) => e.domain === 'media_player'),
  });

  const quickResumeItems = useMemo(() => {
    const items: Array<{
      id: string; title: string; subtitle: string; type: 'audiobook' | 'music'; progress?: number;
    }> = [];

    if (absLastPlayed?.books) {
      for (const book of absLastPlayed.books.slice(0, 3)) {
        // Backend returns progress as 0-100 percentage, normalize to 0-1 ratio for UI
        let progress: number | undefined;
        if (book.progress !== undefined && book.progress !== null) {
          const raw = Number(book.progress);
          progress = raw > 1 ? raw / 100 : raw;
        }
        items.push({
          id: `abs-${book.id}`, title: book.title, subtitle: book.author,
          type: 'audiobook', progress,
        });
      }
    }
    if (maRecent?.recent) {
      for (const item of maRecent.recent.slice(0, 3)) {
        items.push({
          id: `ma-${item.uri}`, title: item.name, subtitle: item.artist,
          type: 'music',
        });
      }
    }
    return items.slice(0, 6);
  }, [absLastPlayed, maRecent]);

  /* ── media status polling ───────────────────────────────────── */

  const fetchMediaStatus = useCallback(async () => {
    try {
      const resp = await api.mediaStatus();
      if (resp.status === 'SUCCESS' && resp.detail) {
        const detail = resp.detail as { active?: Record<string, unknown>; available?: unknown[]; all_players?: unknown[] };
        const active = detail.active;
        if (active) {
          setMediaStatus(active as MediaStatus);
          if (active.volume_level !== undefined) setVolume(Math.round(Number(active.volume_level) * 100));
          if (active.is_volume_muted !== undefined) setMuted(Boolean(active.is_volume_muted));
          if (active.entity_id) {
            setSelectedTarget(String(active.entity_id));
            setLocalMode(false);
          }
        } else if (!selectedTarget) {
          setLocalMode(false);
        }
      }
    } catch { /* ignore */ }
  }, [selectedTarget]);

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    fetchMediaStatus();
    const interval = setInterval(fetchMediaStatus, 10000);
    return () => clearInterval(interval);
  }, [fetchMediaStatus]);
  /* eslint-enable react-hooks/set-state-in-effect */

  /* ── device selection ───────────────────────────────────────────── */

  const handleDeviceSelect = useCallback((entityId: string) => {
    trigger('light');
    setSelectedTarget(entityId);
    setLocalMode(false);
    setError(null);
  }, [trigger]);

  /* ── transport helpers ────────────────────────────────────────── */

  const sendTransport = useCallback(async (command: string) => {
    if (!selectedTarget) { setError('No media player selected'); return; }
    trigger('light');
    setLoading(command);
    setError(null);
    try {
      const resp = await api.mediaTransport({ entity_id: selectedTarget, command });
      if (resp.status === 'FAILURE') setError(resp.message || 'Command failed');
      await fetchMediaStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Command failed');
    } finally { setLoading(null); }
  }, [selectedTarget, trigger, fetchMediaStatus]);

  const playMedia = useCallback(async (query: string, mediaType?: string) => {
    if (!selectedTarget) { setError('Select a device first'); return; }
    trigger('heavy');
    setError(null);
    try {
      const resp = await api.mediaPlay({ entity_id: selectedTarget, query, media_type: mediaType });
      if (resp.status === 'FAILURE') setError(resp.message || 'Playback failed');
      else await fetchMediaStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Playback failed');
    }
  }, [selectedTarget, trigger, fetchMediaStatus]);

  const playAudiobook = useCallback(async (bookId: string) => {
    if (!selectedTarget) { setError('Select a device first'); return; }
    trigger('heavy');
    setError(null);
    try {
      const resp = await api.playAudiobook({ book_id: bookId, entity_id: selectedTarget, resume: true });
      if (resp.status === 'FAILURE') setError(resp.message || 'Playback failed');
      else await fetchMediaStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Playback failed');
    }
  }, [selectedTarget, trigger, fetchMediaStatus]);

  const playPlaylist = useCallback(async (uri: string) => {
    if (!selectedTarget) { setError('Select a device first'); return; }
    trigger('heavy');
    setError(null);
    try {
      const resp = await api.playPlaylist({ playlist_uri: uri, entity_id: selectedTarget });
      if (resp.status === 'FAILURE') setError(resp.message || 'Playback failed');
      else await fetchMediaStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Playback failed');
    }
  }, [selectedTarget, trigger, fetchMediaStatus]);

  const playLocal = useCallback((id: string, title: string, subtitle: string, type: 'audiobook' | 'music', source: 'abs' | 'ma') => {
    trigger('heavy');
    setError(null);
    setLocalTrack({ id, title, subtitle, type, source });
  }, [trigger]);

  const handleVolume = useCallback(async (v: number) => {
    if (!selectedTarget) return;
    setVolume(v);
    try { await api.mediaTransport({ entity_id: selectedTarget, command: 'volume_set', volume_level: v / 100 }); }
    catch { /* ignore */ }
  }, [selectedTarget]);

  const toggleMute = useCallback(async () => {
    if (!selectedTarget) return;
    const newMuted = !muted;
    setMuted(newMuted);
    try { await api.mediaTransport({ entity_id: selectedTarget, command: 'volume_mute', volume_level: newMuted ? 0 : volume / 100 }); }
    catch { /* ignore */ }
  }, [selectedTarget, muted, volume]);

  /* ── render ───────────────────────────────────────────────────── */

  return (
    <div className="space-y-6 max-w-4xl mx-auto pb-24">
      <h1 className="text-2xl font-bold text-white">Media</h1>

      {error && (
        <div className="bg-red-500/20 border border-red-500/30 rounded-xl p-3 text-red-400 text-sm flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="ml-3 underline text-xs shrink-0">Dismiss</button>
        </div>
      )}

      {/* 1. Device Selector */}
      <DeviceSelector
        selectedTarget={selectedTarget}
        entities={entities}
        onDeviceSelect={handleDeviceSelect}
        localMode={localMode}
        onLocalToggle={setLocalMode}
      />

      {/* 2. Active Player Header */}
      <NowPlayingCard
        mediaStatus={mediaStatus}
        selectedTarget={selectedTarget}
        volume={volume}
        muted={muted}
        loading={loading}
        onPrevious={() => sendTransport('previous')}
        onTogglePlay={() => sendTransport(mediaStatus?.state === 'playing' ? 'pause' : 'play')}
        onNext={() => sendTransport('next')}
        onVolumeChange={handleVolume}
        onMuteToggle={toggleMute}
      />

      {/* Local Audio Player */}
      {localTrack && (
        <LocalAudioPlayer initialTrack={localTrack} />
      )}

      {/* 3. Jump Back In */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Jump Back In</h2>
          {(maRecentLoading || absLoading) && quickResumeItems.length === 0 && (
            <button onClick={() => {
              queryClient.invalidateQueries({ queryKey: ['ma-recent'] });
              queryClient.invalidateQueries({ queryKey: ['abs-last-played'] });
            }} className="text-xs text-cyan-400 hover:text-cyan-300 flex items-center gap-1">
              <Loader2 size={12} className="animate-spin" /> Refresh
            </button>
          )}
        </div>
        {maRecentLoading || absLoading ? (
          loadingSection()
        ) : quickResumeItems.length === 0 ? (
          <div className="glass-panel rounded-2xl p-8 text-center"><p className="text-sm text-slate-400 mb-2">No recently played content</p><p className="text-xs text-slate-600">Requires Music Assistant or Audiobookshelf credentials to be configured in the Identity service.</p></div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {quickResumeItems.map((item) => {
              const id = item.id;
              const handlePlay = item.type === 'audiobook'
                ? () => {
                    if (localMode) {
                      playLocal(item.id.replace('abs-', ''), item.title, item.subtitle, 'audiobook', 'abs');
                      return;
                    }
                    if (!selectedTarget) {
                      playLocal(item.id.replace('abs-', ''), item.title, item.subtitle, 'audiobook', 'abs');
                      return;
                    }
                    playAudiobook(item.id.replace('abs-', ''));
                  }
                : () => {
                    if (localMode) {
                      playLocal(item.id.replace('ma-', ''), item.title, item.subtitle, 'music', 'ma');
                      return;
                    }
                    if (!selectedTarget) {
                      playLocal(item.id.replace('ma-', ''), item.title, item.subtitle, 'music', 'ma');
                      return;
                    }
                    playMedia(item.title, 'music');
                  };
              return (
                <QuickResumeItem
                  key={id} item={item}
                  onPlay={handlePlay} isDisabled={false}
                  isLoading={loading !== null}
                />
              );
            })}
          </div>
        )}
      </section>

      {/* 4. Playlists */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Playlists</h2>
          {!maPlaylists && !maPlaylistsLoading && (
            <button onClick={() => queryClient.invalidateQueries({ queryKey: ['ma-playlists'] })}
              className="text-xs text-cyan-400 hover:text-cyan-300 flex items-center gap-1">
              <Loader2 size={12} className="animate-spin" /> Refresh
            </button>
          )}
        </div>
        {maPlaylistsLoading ? loadingSection() : (
          !maPlaylists?.playlists?.length ? (
            quickResumeItems.length === 0
              ? <div className="glass-panel rounded-2xl p-8 text-center"><p className="text-sm text-slate-400 mb-2">No playlists available</p><p className="text-xs text-slate-600">Requires Music Assistant credentials to be configured in the Identity service.</p></div>
              : emptySection('No playlists available')
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {maPlaylists.playlists.map((pl) => (
                <PlaylistItem
                  key={pl.uri} name={pl.name} trackCount={pl.items}
                  onPlay={() => {
                    if (localMode) {
                      playLocal(pl.uri, pl.name, pl.items > 0 ? `${pl.items} tracks` : 'Playlist', 'music', 'ma');
                      return;
                    }
                    if (!selectedTarget) {
                      playLocal(pl.uri, pl.name, pl.items > 0 ? `${pl.items} tracks` : 'Playlist', 'music', 'ma');
                      return;
                    }
                    playPlaylist(pl.uri);
                  }}
                  isDisabled={false} isLoading={loading !== null}
                />
              ))}
            </div>
          )
        )}
      </section>

      {/* 4. Browse All Media */}
      <button
        onClick={() => { setShowMediaPicker(true); }}
        className={`w-full py-4 rounded-2xl bg-gradient-to-r from-cyan-500/10 to-purple-500/10 border border-cyan-500/20 text-cyan-400 font-medium hover:from-cyan-500/20 hover:to-purple-500/20 transition-all flex items-center justify-center gap-2`}
      >
        <Library size={20} />
        Browse All Media
      </button>

      {/* 5. Explorer Modal */}
      <MediaExplorerModal
        show={showMediaPicker}
        onClose={() => setShowMediaPicker(false)}
        playAudiobook={playAudiobook}
        playPlaylist={playPlaylist}
        playMedia={playMedia}
        playLocal={playLocal}
        selectedTarget={selectedTarget}
        localMode={localMode}
      />
    </div>
  );
};

export default Media;
