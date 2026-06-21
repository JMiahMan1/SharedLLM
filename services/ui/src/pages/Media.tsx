import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Play, Pause, Volume2, Volume1, VolumeX, Cast,
  Music, BookOpen, List, Loader2, X, Library, Search,
  SkipBack as SkipBackIcon, SkipForward as SkipForwardIcon,
  ChevronRight, Grid3X3, Clock, Headphones, Heart, Square
} from 'lucide-react';
import { api } from '../services/api';
import { useHaptics } from '../hooks/useHaptics';
import { storageGetSync } from '../lib/storage';
import {
  destroy as destroyWebPlayer,
  installPageHideListener,
  releaseControl,
  handleVisibilityChange,
} from '../lib/webPlayer';
import { useMAWebPlayer } from '../lib/maWebPlayer';

interface MediaStatus {
  entity_id?: string;
  friendly_name?: string;
  state?: string;
  media_title?: string;
  media_artist?: string;
  media_album?: string;
  volume_level?: number;
  is_volume_muted?: boolean;
  position?: number;
  duration?: number;
  entity_picture?: string;
  media_content_id?: string;
  media_type?: string;
}

interface MediaEntity {
  entity_id: string;
  friendly_name: string;
  state: string;
  domain: string;
}

interface TrackDetail {
  item_id: string;
  name: string;
  uri: string;
  favorite: boolean;
  media_type: string;
  artists?: Array<{ name: string }>;
  album?: { name: string };
  podcast?: { name: string };
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
        {/* Web Player */}
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
            <p className="text-white text-sm font-medium truncate">Web Player</p>
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
  localMode,
  volume,
  muted,
  loading,
  currentTime = 0,
  duration = 0,
  isFavorite = false,
  onPrevious,
  onTogglePlay,
  onNext,
  onVolumeChange,
  onMuteToggle,
  onFavoriteToggle,
  onSeek,
  onStopPlayback,
}: {
  mediaStatus: MediaStatus | null;
  selectedTarget: string;
  localMode?: boolean;
  volume: number;
  muted: boolean;
  loading: string | null;
  currentTime?: number;
  duration?: number;
  isFavorite?: boolean;
  onPrevious: () => void;
  onTogglePlay: () => void;
  onNext: () => void;
  onVolumeChange: (v: number) => void;
  onMuteToggle: () => void;
  onFavoriteToggle?: () => void;
  onSeek?: (time: number) => void;
  onStopPlayback?: () => void;
}) => {
  const nowPlaying = mediaStatus?.state === 'playing' || mediaStatus?.state === 'paused';

  const formatTime = (seconds: number) => {
    if (!seconds || isNaN(seconds)) return '0:00';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  const coverUrl = useMemo(() => {
    if (!mediaStatus?.entity_picture) return null;
    const path = mediaStatus.entity_picture;
    const apiToken = storageGetSync('jarvis_api_key') ?? '';
    return `/api/media/imageproxy?path=${encodeURIComponent(path)}${apiToken ? `&token=${encodeURIComponent(apiToken)}` : ''}`;
  }, [mediaStatus?.entity_picture]);

  return (
    <div className="glass-panel rounded-2xl p-5 border border-cyan-500/20 relative overflow-visible">
      <div className="absolute inset-0 bg-gradient-to-br from-cyan-500/5 via-transparent to-purple-500/5 pointer-events-none rounded-2xl" />

      <div className="relative flex flex-col sm:flex-row sm:items-center gap-4">
        {/* cover art or icon */}
        <div className="w-16 h-16 sm:w-20 sm:h-20 rounded-xl bg-gradient-to-br from-cyan-500/30 to-purple-500/30 flex items-center justify-center shrink-0 shadow-lg shadow-cyan-500/10 relative overflow-hidden group">
          {coverUrl ? (
            <>
              <div className="absolute inset-0 bg-cyan-500/20 blur-xl opacity-50 group-hover:opacity-80 transition-opacity" />
              <img src={coverUrl} alt="Cover art" className="w-full h-full object-cover relative z-10" />
            </>
          ) : nowPlaying ? (
            <Music size={28} className="text-cyan-400" />
          ) : (
            <Play size={28} className="text-cyan-400" />
          )}
        </div>

        {/* metadata + transport */}
        <div className="flex-1 min-w-0">
          {nowPlaying ? (
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-white font-medium text-lg truncate">{mediaStatus.media_title || 'Unknown Title'}</p>
                <p className="text-sm text-slate-400 truncate">{mediaStatus.media_artist || 'Unknown Artist'}</p>
              </div>
              {onFavoriteToggle && (
                <button
                  onClick={onFavoriteToggle}
                  className={`p-2 rounded-xl hover:bg-white/5 transition-all shrink-0 ${
                    isFavorite ? 'text-red-500 scale-110' : 'text-slate-400 hover:text-slate-200'
                  }`}
                  aria-label={isFavorite ? "Remove from favorites" : "Add to favorites"}
                >
                  <Heart size={20} fill={isFavorite ? "currentColor" : "none"} />
                </button>
              )}
            </div>
          ) : (
            <>
              <p className="text-white font-medium text-lg">No Active Playback</p>
              <p className="text-sm text-slate-400">
                {localMode ? 'Select a track below to stream locally' : 'Select a device and content to begin'}
              </p>
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
            {onStopPlayback && (
              <button onClick={onStopPlayback}
                className="text-red-400 hover:text-red-300 transition-colors p-2 rounded-lg hover:bg-white/5 ml-1" aria-label="Stop playback">
                <Square size={20} />
              </button>
            )}
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

      {nowPlaying && duration > 0 && (
        <div className="mt-4 pt-3 border-t border-white/5">
          <div
            className={`w-full h-2 bg-white/10 rounded-full relative group ${onSeek ? 'cursor-pointer' : ''}`}
            onClick={(e) => {
              if (!onSeek) return;
              const rect = e.currentTarget.getBoundingClientRect();
              const x = e.clientX - rect.left;
              const percent = x / rect.width;
              onSeek(percent * duration);
            }}
          >
            <div
              className="h-full bg-gradient-to-r from-cyan-400 to-purple-400 rounded-full transition-all relative"
              style={{ width: `${Math.min(100, (currentTime / duration) * 100)}%` }}
            >
              {onSeek && (
                <div className="absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full shadow-lg opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none" />
              )}
            </div>
          </div>
          <div className="flex justify-between mt-1.5 text-[10px] text-slate-500 font-mono">
            <span>{formatTime(currentTime)}</span>
            <span>{formatTime(duration)}</span>
          </div>
        </div>
      )}

      {localMode ? (
        <div className="relative mt-4 pt-3 border-t border-white/5">
          <div className="flex items-center justify-center gap-2 py-2 rounded-xl bg-cyan-500/10 border border-cyan-500/20 text-cyan-400 text-sm">
            <Music size={14} className="animate-pulse" />
            Web Player (Browser Audio) Active. Ready to stream locally.
          </div>
        </div>
      ) : !selectedTarget ? (
        <div className="relative mt-4 pt-3 border-t border-white/5">
          <div className="flex items-center justify-center gap-2 py-2 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-400 text-sm">
            <Cast size={14} />
            Select a device above to enable playback
          </div>
        </div>
      ) : null}
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

  // MA Web Player (sendspin-js)
  const maPlayer = useMAWebPlayer();

  // Local player states
  const [localIsPlaying, setLocalIsPlaying] = useState(false);
  const [localVolume, setLocalVolume] = useState(70);
  const [localMuted, setLocalMuted] = useState(false);
  const [localCurrentTime, setLocalCurrentTime] = useState(0);
  const [localDuration, setLocalDuration] = useState(0);
  const localProgressTimerRef = useRef<number | null>(null);

  // Debug state for troubleshooting
  const [audioDebug, setAudioDebug] = useState<{
    events: Array<{ time: string; type: string; message?: string; code?: number }>;
    streamUrl: string | null;
    audioState: string;
  }>({ events: [], streamUrl: null, audioState: 'idle' });
  const [showDebug, setShowDebug] = useState(false);

    // Music Assistant metadata & favorites state
  const [detailedMetadata, setDetailedMetadata] = useState<TrackDetail | null>(null);

  const activeUri = useMemo(() => {
    if (localMode) {
      return localTrack?.source === 'ma' ? localTrack.id : null;
    } else {
      return mediaStatus?.media_content_id || null;
    }
  }, [localMode, localTrack, mediaStatus]);

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!activeUri || !activeUri.includes('://')) {
      setDetailedMetadata(null);
      return;
    }
    let active = true;
    api.getMediaDetail(activeUri)
      .then((data) => {
        if (active) {
          const detail = (data && data.result ? data.result : data) as unknown as TrackDetail;
          setDetailedMetadata(detail);
        }
      })
      .catch((err) => {
        console.error('Failed to fetch media detail:', err);
        if (active) setDetailedMetadata(null);
      });
    return () => {
      active = false;
    };
  }, [activeUri]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const handleFavoriteToggle = useCallback(async () => {
    if (!activeUri) return;
    const currentFavorite = Boolean(detailedMetadata?.favorite);
    const targetFavorite = !currentFavorite;
    
    // Optimistic update
    setDetailedMetadata((prev) => prev ? { ...prev, favorite: targetFavorite } : null);
    
    try {
      const resp = await api.setMediaFavorite(activeUri, targetFavorite);
      if (resp && (resp.status === 'SUCCESS' || resp.favorite !== undefined)) {
        setDetailedMetadata((prev) => prev ? { ...prev, favorite: resp.favorite } : null);
      }
    } catch (err) {
      console.error('Failed to toggle favorite:', err);
      setDetailedMetadata((prev) => prev ? { ...prev, favorite: currentFavorite } : null);
      setError('Failed to update favorite status');
    }
  }, [activeUri, detailedMetadata]);

  const detailedSubtitle = useMemo(() => {
    const baseArtist = localMode ? localTrack?.subtitle : mediaStatus?.media_artist;
    const baseAlbum = localMode ? '' : mediaStatus?.media_album;
    
    const artist = detailedMetadata?.artists?.map((a) => a.name).join(', ') || baseArtist || '';
    const album = detailedMetadata?.album?.name || baseAlbum || '';
    
    const podcast = detailedMetadata?.podcast?.name;
    const albumOrPodcast = podcast || album;

    if (artist && albumOrPodcast) {
      return `${artist} • ${albumOrPodcast}`;
    }
    return artist || albumOrPodcast || '';
  }, [localMode, localTrack, mediaStatus, detailedMetadata]);

  const detailedTitle = useMemo(() => {
    const baseTitle = localMode ? localTrack?.title : mediaStatus?.media_title;
    return detailedMetadata?.name || baseTitle || 'Unknown Title';
  }, [localMode, localTrack, mediaStatus, detailedMetadata]);

  // Sync volume with sendspin player
  useEffect(() => {
    if (maPlayer.connected) {
      maPlayer.setVolume(localVolume / 100);
    }
  }, [localVolume, maPlayer.connected, maPlayer]);

  // Sync mute with sendspin player
  useEffect(() => {
    if (maPlayer.connected) {
      maPlayer.setMuted(localMuted);
    }
  }, [localMuted, maPlayer.connected, maPlayer]);

  // Handle local playback progress tracking
  useEffect(() => {
    if (localIsPlaying && maPlayer.audioRef.current) {
      let counter = 0;
      localProgressTimerRef.current = window.setInterval(() => {
        if (maPlayer.audioRef.current && !maPlayer.audioRef.current.paused) {
          const pos = maPlayer.audioRef.current.currentTime;
          setLocalCurrentTime(pos);
          
          counter++;
          if (counter >= 5 && localTrack) {
            counter = 0;
            api.syncMediaState({
              entity_id: 'web_player',
              state: 'playing',
              media_type: localTrack.type,
              media_content_id: localTrack.id,
              media_title: localTrack.title,
              media_artist: localTrack.subtitle,
              position: pos,
              duration: maPlayer.audioRef.current.duration || 0,
              volume_level: localVolume / 100,
              is_volume_muted: localMuted
            }).catch(err => console.error('Failed to sync progress:', err));
          }
        }
      }, 1000);
    }
    return () => {
      if (localProgressTimerRef.current) {
        clearInterval(localProgressTimerRef.current);
      }
    };
  }, [localIsPlaying, maPlayer.audioRef, localTrack, localVolume, localMuted]);

  // Cleanup on unmount
  useEffect(() => {
    installPageHideListener();
    const handleVis = () => handleVisibilityChange();
    document.addEventListener('visibilitychange', handleVis);
    return () => {
      document.removeEventListener('visibilitychange', handleVis);
      destroyWebPlayer();
    };
  }, []);

  const [remoteCurrentTime, setRemoteCurrentTime] = useState(0);
  const [remoteDuration, setRemoteDuration] = useState(0);

  /* eslint-disable react-hooks/set-state-in-effect */
  // Sync remote time when mediaStatus changes
  useEffect(() => {
    if (mediaStatus) {
      setRemoteCurrentTime(mediaStatus.position || 0);
      setRemoteDuration(mediaStatus.duration || 0);
    } else {
      setRemoteCurrentTime(0);
      setRemoteDuration(0);
    }
  }, [mediaStatus]);

  // Tick remote time locally while playing to keep progress smooth between status polls
  useEffect(() => {
    let timer: number | null = null;
    if (mediaStatus?.state === 'playing' && !localMode) {
      timer = window.setInterval(() => {
        setRemoteCurrentTime((prev) => {
          const dur = mediaStatus.duration || 0;
          if (dur > 0 && prev >= dur) return dur;
          return prev + 1;
        });
      }, 1000);
    }
    return () => {
      if (timer) clearInterval(timer);
    };
  }, [mediaStatus?.state, mediaStatus?.duration, localMode]);

  // If localMode is turned off, pause the local player automatically so it doesn't leak audio
  useEffect(() => {
    if (!localMode && localIsPlaying) {
      setLocalIsPlaying(false);
      maPlayer.pause();
      if (localTrack) {
        api.syncMediaState({
          entity_id: 'web_player',
          state: 'paused',
          media_type: localTrack.type,
          media_content_id: localTrack.id,
          media_title: localTrack.title,
          media_artist: localTrack.subtitle,
          position: maPlayer.position || 0,
          duration: localDuration,
          volume_level: localVolume / 100,
          is_volume_muted: localMuted
        }).catch(err => console.error('Failed to sync local pause on mode change:', err));
      }
    }
  }, [localMode, localTrack, localDuration, localVolume, localMuted, localIsPlaying, maPlayer]);
  /* eslint-enable react-hooks/set-state-in-effect */

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
        const detail = resp.detail as {
          active?: (MediaStatus & { position?: number; duration?: number; media_content_id?: string; media_type?: string }) | null;
          available?: MediaStatus[];
          all_players?: MediaStatus[];
        };
        const allPlayers = detail.all_players || [];
        const active = detail.active;
        
        if (active) {
          if (active.entity_id === 'web_player') {
            setLocalMode(true);
            setSelectedTarget('');
            
            // Sync local player states
            if (active.media_content_id && (!localTrack || localTrack.id !== active.media_content_id)) {
              const idClean = active.media_content_id;
              const title = active.media_title || 'Unknown Title';
              const subtitle = active.media_artist || 'Unknown Artist';
              const type = active.media_type as 'audiobook' | 'music';
              const source = active.media_type === 'audiobook' ? 'abs' : 'ma';
              
              setLocalTrack({ id: idClean, title, subtitle, type, source });
              setLocalIsPlaying(active.state === 'playing');
              setLocalCurrentTime(active.position || 0);
            } else if (localTrack) {
              const backendPlaying = active.state === 'playing';
              if (backendPlaying !== localIsPlaying) {
                setLocalIsPlaying(backendPlaying);
                if (maPlayer.connected) {
                  if (backendPlaying) {
                    maPlayer.cmdPlay().catch(() => {});
                  } else {
                    maPlayer.cmdPause();
                  }
                }
              }
            }
            
            if (active.volume_level !== undefined && active.volume_level !== null) {
              setLocalVolume(Math.round(active.volume_level * 100));
            }
            if (active.is_volume_muted !== undefined) {
              setLocalMuted(active.is_volume_muted);
            }
          } else {
            setMediaStatus(active);
            setLocalMode(false);
            if (active.entity_id) {
              setSelectedTarget(String(active.entity_id));
            }
            if (active.volume_level !== undefined && active.volume_level !== null) {
              setVolume(Math.round(Number(active.volume_level) * 100));
            }
            if (active.is_volume_muted !== undefined) {
              setMuted(Boolean(active.is_volume_muted));
            }
          }
        } else {
          // If no active target is reported, we check standard HA player target matching
          const targetPlayer = selectedTarget ? allPlayers.find(p => p.entity_id === selectedTarget) : null;
          if (targetPlayer) {
            setMediaStatus(targetPlayer);
            if (targetPlayer.volume_level !== undefined && targetPlayer.volume_level !== null) {
              setVolume(Math.round(Number(targetPlayer.volume_level) * 100));
            }
            if (targetPlayer.is_volume_muted !== undefined) {
              setMuted(Boolean(targetPlayer.is_volume_muted));
            }
          } else {
            setMediaStatus(null);
          }
        }
      }
    } catch { /* ignore */ }
    /* eslint-disable react-hooks/exhaustive-deps -- maPlayer is accessed for current value, not as a dependency */
  }, [selectedTarget, localTrack, localIsPlaying]);

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    fetchMediaStatus();
    const interval = setInterval(fetchMediaStatus, 3000);
    return () => clearInterval(interval);
  }, [fetchMediaStatus]);
  /* eslint-enable react-hooks/set-state-in-effect */

  

  /* ── device selection ───────────────────────────────────────────── */

  const handleDeviceSelect = useCallback((entityId: string) => {
    trigger('light');
    setSelectedTarget(entityId);
    setLocalMode(false);
    setError(null);
    api.syncMediaState({
      entity_id: entityId,
      state: 'idle'
    }).catch(err => console.error('Failed to sync device select:', err));
  }, [trigger]);

  const handleLocalToggle = useCallback((mode: boolean) => {
    setLocalMode(mode);
    if (mode) {
      setSelectedTarget('');
      api.syncMediaState({
        entity_id: 'web_player',
        state: localTrack ? (localIsPlaying ? 'playing' : 'paused') : 'idle',
        media_type: localTrack?.type,
        media_content_id: localTrack?.id,
        media_title: localTrack?.title,
        media_artist: localTrack?.subtitle,
        position: localCurrentTime,
        duration: localDuration,
        volume_level: localVolume / 100,
        is_volume_muted: localMuted
      }).catch(err => console.error('Failed to sync local toggle:', err));
    }
  }, [localTrack, localIsPlaying, localCurrentTime, localDuration, localVolume, localMuted]);

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
    setLoading('play');
    setError(null);
    try {
      const resp = await api.mediaPlay({ entity_id: selectedTarget, query, media_type: mediaType });
      if (resp.status === 'FAILURE') setError(resp.message || 'Playback failed');
      else await fetchMediaStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Playback failed');
    } finally {
      setLoading(null);
    }
  }, [selectedTarget, trigger, fetchMediaStatus]);

  const playAudiobook = useCallback(async (bookId: string) => {
    if (!selectedTarget) { setError('Select a device first'); return; }
    trigger('heavy');
    setLoading('play');
    setError(null);
    try {
      const resp = await api.playAudiobook({ book_id: bookId, entity_id: selectedTarget, resume: true });
      if (resp.status === 'FAILURE') setError(resp.message || 'Playback failed');
      else await fetchMediaStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Playback failed');
    } finally {
      setLoading(null);
    }
  }, [selectedTarget, trigger, fetchMediaStatus]);

  const playPlaylist = useCallback(async (uri: string) => {
    if (!selectedTarget) { setError('Select a device first'); return; }
    trigger('heavy');
    setLoading('play');
    setError(null);
    try {
      const resp = await api.playPlaylist({ playlist_uri: uri, entity_id: selectedTarget });
      if (resp.status === 'FAILURE') setError(resp.message || 'Playback failed');
      else await fetchMediaStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Playback failed');
    } finally {
      setLoading(null);
    }
  }, [selectedTarget, trigger, fetchMediaStatus]);

 const playLocal = useCallback(async (id: string, title: string, subtitle: string, type: 'audiobook' | 'music', source: 'abs' | 'ma') => {
    trigger('heavy');
    setError(null);
    const idClean = id.replace('abs-', '').replace('ma-', '');
    setLocalTrack({ id: idClean, title, subtitle, type, source });
    setLocalVolume((prev) => { setLocalIsPlaying(true); return prev; });

    // Initialize player if not connected (establishes Sendspin + JSON-RPC)
    if (!maPlayer.isConnected) {
      await maPlayer.connect();
    }

    // Set initial volume and muted state
    maPlayer.setVolume(localVolume);
    maPlayer.setMuted(localMuted);

    // Build the MA-compatible URI (ABS books get converted)
    let mediaUri = idClean;
    if (source === 'abs') {
      // ABS books need to be converted to MA URIs for play_media
      mediaUri = `audiobookshelf://${idClean}`;
    }

    // Send play_media via JSON-RPC to tell MA to queue and start this track
    await maPlayer.playMedia(mediaUri);

    try {
      await api.syncMediaState({
        entity_id: 'web_player',
        state: 'playing',
        media_type: type,
        media_content_id: idClean,
        media_title: title,
        media_artist: subtitle,
        position: 0.0,
        duration: 0.0,
        volume_level: localVolume / 100,
        is_volume_muted: localMuted
      });
    } catch (err) {
      console.error('Failed to sync local play:', err);
    }
  }, [trigger, localVolume, localMuted, maPlayer]);

  const toggleLocalPlay = useCallback(() => {
    if (!localTrack) return;
    const nextPlaying = !localIsPlaying;
    if (localIsPlaying) {
      setLocalIsPlaying(false);
      maPlayer.pause();
    } else {
      setLocalIsPlaying(true);
      (document.activeElement as HTMLElement)?.setAttribute('data-webplayer-interact', 'true');
      if (!maPlayer.isConnected) {
        maPlayer.connect();
      }
      // Re-send play_media in case it was lost
      const mediaUri = localTrack.source === 'abs'
        ? `audiobookshelf://${localTrack.id}`
        : localTrack.id;
      maPlayer.playMedia(mediaUri);
    }
    api.syncMediaState({
      entity_id: 'web_player',
      state: nextPlaying ? 'playing' : 'paused',
      media_type: localTrack.type,
      media_content_id: localTrack.id,
      media_title: localTrack.title,
      media_artist: localTrack.subtitle,
      position: localCurrentTime,
      duration: localDuration,
      volume_level: localVolume / 100,
      is_volume_muted: localMuted
    }).catch(err => console.error('Failed to sync toggleLocalPlay:', err));
  }, [localTrack, localIsPlaying, localDuration, localVolume, localMuted, localCurrentTime, maPlayer]);

  const handleLocalVolume = useCallback((v: number) => {
    setLocalVolume(v);
    setLocalMuted(false);
    maPlayer.setVolume(v);
    if (localTrack) {
      api.syncMediaState({
        entity_id: 'web_player',
        state: localIsPlaying ? 'playing' : 'paused',
        media_type: localTrack.type,
        media_content_id: localTrack.id,
        media_title: localTrack.title,
        media_artist: localTrack.subtitle,
        position: localCurrentTime,
        duration: localDuration,
        volume_level: v / 100,
        is_volume_muted: false
      }).catch(err => console.error('Failed to sync volume:', err));
    }
  }, [localTrack, localIsPlaying, localCurrentTime, localDuration, maPlayer]);

  const toggleLocalMute = useCallback(() => {
    const nextMuted = !localMuted;
    setLocalMuted(nextMuted);
    maPlayer.setMuted(nextMuted);
    if (localTrack) {
      api.syncMediaState({
        entity_id: 'web_player',
        state: localIsPlaying ? 'playing' : 'paused',
        media_type: localTrack.type,
        media_content_id: localTrack.id,
        media_title: localTrack.title,
        media_artist: localTrack.subtitle,
        position: localCurrentTime,
        duration: localDuration,
        volume_level: localVolume / 100,
        is_volume_muted: nextMuted
      }).catch(err => console.error('Failed to sync mute:', err));
    }
  }, [localTrack, localIsPlaying, localCurrentTime, localDuration, localVolume, localMuted, maPlayer]);

  const handleLocalSeek = useCallback((time: number) => {
    maPlayer.seek(time);
    setLocalCurrentTime(time);
    if (localTrack) {
      api.syncMediaState({
        entity_id: 'web_player',
        state: localIsPlaying ? 'playing' : 'paused',
        media_type: localTrack.type,
        media_content_id: localTrack.id,
        media_title: localTrack.title,
        media_artist: localTrack.subtitle,
        position: time,
        duration: localDuration,
        volume_level: localVolume / 100,
        is_volume_muted: localMuted
      }).catch(err => console.error('Failed to sync seek:', err));
    }
  }, [localTrack, localIsPlaying, localDuration, localVolume, localMuted, maPlayer]);

  const skipLocalBack = useCallback(() => {
    const currentTime = maPlayer.position || 0;
    if (currentTime > 5) {
      maPlayer.seek(0);
      setLocalCurrentTime(0);
      if (localTrack) {
        api.syncMediaState({
          entity_id: 'web_player',
          state: localIsPlaying ? 'playing' : 'paused',
          media_type: localTrack.type,
          media_content_id: localTrack.id,
          media_title: localTrack.title,
          media_artist: localTrack.subtitle,
          position: 0,
          duration: localDuration,
          volume_level: localVolume / 100,
          is_volume_muted: localMuted
        }).catch(err => console.error('Failed to sync skip back:', err));
      }
    }
  }, [localTrack, localIsPlaying, localDuration, localVolume, localMuted, maPlayer]);

  const skipLocalForward = useCallback(() => {
    const currentTime = maPlayer.position || 0;
    const dur = localDuration || 0;
    if (dur > 0) {
      const newTime = Math.min(currentTime + 30, dur);
      maPlayer.seek(newTime);
      setLocalCurrentTime(newTime);
      if (localTrack) {
        api.syncMediaState({
          entity_id: 'web_player',
          state: localIsPlaying ? 'playing' : 'paused',
          media_type: localTrack.type,
          media_content_id: localTrack.id,
          media_title: localTrack.title,
          media_artist: localTrack.subtitle,
          position: newTime,
          duration: localDuration,
          volume_level: localVolume / 100,
          is_volume_muted: localMuted
        }).catch(err => console.error('Failed to sync skip forward:', err));
      }
    }
  }, [localTrack, localIsPlaying, localDuration, localVolume, localMuted, maPlayer]);

  const handleStopPlayback = useCallback(() => {
    if (localTrack) {
      releaseControl(false);
      api.syncMediaState({
        entity_id: 'web_player',
        state: 'idle',
        media_type: localTrack.type,
        media_content_id: localTrack.id,
        media_title: localTrack.title,
        media_artist: localTrack.subtitle,
        position: 0,
        duration: localDuration,
        volume_level: localVolume / 100,
        is_volume_muted: localMuted
      }).catch(err => console.error('Failed to sync stop:', err));
    }
    setLocalTrack(null);
    setLocalIsPlaying(false);
    setLocalCurrentTime(0);
    setLocalDuration(0);
    maPlayer.disconnect();
  }, [localTrack, localDuration, localVolume, localMuted, maPlayer]);

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
        onLocalToggle={handleLocalToggle}
      />

      {/* 2. Active Player Header */}
      <NowPlayingCard
        mediaStatus={
          localMode
            ? localTrack
              ? {
                  entity_id: 'web_player',
                  state: localIsPlaying ? 'playing' : 'paused',
                  media_title: detailedTitle,
                  media_artist: detailedSubtitle,
                  media_type: localTrack.type,
                  volume_level: localVolume / 100,
                  is_volume_muted: localMuted,
                }
              : null
            : mediaStatus
              ? {
                  ...mediaStatus,
                  media_title: detailedTitle,
                  media_artist: detailedSubtitle,
                }
              : null
        }
        selectedTarget={selectedTarget}
        localMode={localMode}
        volume={localMode ? localVolume : volume}
        muted={localMode ? localMuted : muted}
        loading={localMode ? null : loading}
        currentTime={localMode ? localCurrentTime : remoteCurrentTime}
        duration={localMode ? localDuration : remoteDuration}
        isFavorite={Boolean(detailedMetadata?.favorite)}
        onPrevious={localMode ? skipLocalBack : () => sendTransport('previous')}
        onTogglePlay={
          localMode
            ? toggleLocalPlay
            : () => sendTransport(mediaStatus?.state === 'playing' ? 'pause' : 'resume')
        }
        onNext={localMode ? skipLocalForward : () => sendTransport('next')}
        onVolumeChange={localMode ? handleLocalVolume : handleVolume}
        onMuteToggle={localMode ? toggleLocalMute : toggleMute}
        onFavoriteToggle={activeUri ? handleFavoriteToggle : undefined}
        onSeek={localMode ? handleLocalSeek : undefined}
        onStopPlayback={localMode && localTrack ? handleStopPlayback : undefined}
      />

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

      {/* Debug Panel - Audio Events */}
      <div className="mt-6">
        <button
          onClick={() => setShowDebug(!showDebug)}
          className="text-xs text-slate-500 hover:text-slate-300 transition-colors flex items-center gap-1"
        >
          {showDebug ? '▼' : '▶'} Audio Debug ({audioDebug.events.length} events)
        </button>
        {showDebug && (
          <div className="mt-2 glass-panel rounded-xl p-4 max-h-96 overflow-y-auto">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs text-slate-400">
                Stream URL: {audioDebug.streamUrl ? audioDebug.streamUrl.substring(0, 60) + '...' : 'None'}
              </span>
              <button
                onClick={() => setAudioDebug(prev => ({ ...prev, events: [] }))}
                className="text-xs text-red-400 hover:text-red-300"
              >
                Clear
              </button>
            </div>
            {audioDebug.events.length === 0 ? (
              <p className="text-xs text-slate-500 text-center py-4">No audio events yet. Play a track to see debugging info.</p>
            ) : (
              <div className="space-y-1">
                {[...audioDebug.events].reverse().map((evt, i) => (
                  <div
                    key={i}
                    className={`text-xs font-mono py-1 px-2 rounded ${
                      evt.type === 'ERROR'
                        ? 'bg-red-500/10 text-red-400'
                        : evt.type === 'PLAYING' || evt.type === 'PLAY' || evt.type === 'CANPLAY' || evt.type === 'CANPLAYTHROUGH'
                        ? 'bg-green-500/10 text-green-400'
                        : evt.type === 'WAITING' || evt.type === 'STALLED'
                        ? 'bg-yellow-500/10 text-yellow-400'
                        : 'text-slate-300'
                    }`}
                  >
                    <span className="text-slate-500">{evt.time.split('T')[1]?.split('.')[0] || evt.time}</span>{' '}
                    <span className="font-bold">{evt.type}</span>
                    {evt.message && <span className="ml-1">— {evt.message}</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default Media;
