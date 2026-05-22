import { useState, useCallback, useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Play, Pause, Volume2, Volume1, VolumeX, Cast,
  Music, BookOpen, List, Loader2, ChevronDown, X, Library, Search,
  SkipBack as SkipBackIcon, SkipForward as SkipForwardIcon,
  ChevronRight, Grid3X3, Clock, Headphones,
} from 'lucide-react';
import { api } from '../services/api';
import { useHaptics } from '../hooks/useHaptics';

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

/* ── device selector (stand-alone) ──────────────────────────────────── */

const DeviceSelector = ({
  selectedTarget,
  selectedTargetInfo,
  entities,
}: {
  selectedTarget: string;
  selectedTargetInfo?: { name: string; room: string };
  entities: MediaEntity[];
}) => {
  const { trigger } = useHaptics();
  const [open, setOpen] = useState(false);

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

  useEffect(() => {
    if (!open) return;
    const handler = () => setOpen(false);
    const timer = setTimeout(() => document.addEventListener('click', handler), 0);
    return () => { clearTimeout(timer); document.removeEventListener('click', handler); };
  }, [open]);

  return (
    <div className="relative">
      <button
        onClick={(e) => { e.stopPropagation(); setOpen(!open); }}
        className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs transition-all ${
          selectedTarget
            ? 'bg-cyan-500/10 border-cyan-500/30 text-cyan-400'
            : 'bg-white/5 border-white/10 text-slate-400 hover:border-white/20 hover:text-white'
        }`}
      >
        <Cast size={12} />
        <span className="max-w-28 truncate">{selectedTargetInfo?.name || 'Cast To'}</span>
        <ChevronDown size={12} className={`opacity-60 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div
          className="absolute right-0 top-full mt-1.5 w-64 glass-panel rounded-xl border border-white/10 shadow-2xl z-50 overflow-hidden"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="p-1.5 max-h-60 overflow-y-auto custom-scrollbar">
            {targets.map((t) => (
              <button
                key={t.id}
                onClick={() => { trigger('light'); setOpen(false); }}
                className={`w-full flex items-center gap-2.5 p-2 rounded-lg transition-colors text-left ${
                  selectedTarget === t.id ? 'bg-cyan-500/20 border border-cyan-500/30' : 'hover:bg-white/10'
                } ${!t.online ? 'opacity-40' : ''}`}
              >
                <Cast size={14} className="text-slate-400 shrink-0" />
                <div className="min-w-0 flex-1">
                  <p className="text-white text-sm font-medium truncate">{t.name}</p>
                  <p className="text-xs text-slate-500 truncate">{t.room}</p>
                </div>
                <div className={`w-2 h-2 rounded-full shrink-0 ${t.online ? 'bg-green-400' : 'bg-slate-600'}`} />
              </button>
            ))}
            {targets.length === 0 && <p className="text-xs text-slate-500 text-center py-4">No media players found</p>}
          </div>
        </div>
      )}
    </div>
  );
};

/* ── player header ──────────────────────────────────────────────────── */

const NowPlayingCard = ({
  mediaStatus,
  selectedTarget,
  selectedTargetInfo,
  volume,
  muted,
  loading,
  entities,
  onPrevious,
  onTogglePlay,
  onNext,
  onVolumeChange,
  onMuteToggle,
}: {
  mediaStatus: MediaStatus | null;
  selectedTarget: string;
  selectedTargetInfo?: { name: string; room: string };
  volume: number;
  muted: boolean;
  loading: string | null;
  entities: MediaEntity[];
  onPrevious: () => void;
  onTogglePlay: () => void;
  onNext: () => void;
  onVolumeChange: (v: number) => void;
  onMuteToggle: () => void;
}) => {
  const nowPlaying = mediaStatus?.state === 'playing' || mediaStatus?.state === 'paused';

  return (
    <div className="glass-panel rounded-2xl p-5 border border-cyan-500/20 overflow-hidden relative">
      <div className="absolute inset-0 bg-gradient-to-br from-cyan-500/5 via-transparent to-purple-500/5 pointer-events-none" />

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

        {/* volume + device */}
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
          <DeviceSelector selectedTarget={selectedTarget} selectedTargetInfo={selectedTargetInfo} entities={entities} />
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
      <p className="text-xs text-slate-400">{trackCount} {trackCount === 1 ? 'track' : 'tracks'}</p>
    </div>
    <Play size={16} className="text-slate-500 opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
  </button>
);

/* ── explorer modal ─────────────────────────────────────────────────── */

const MediaExplorerModal = ({
  show, onClose, playAudiobook, playPlaylist, playMedia, isDisabled,
}: {
  show: boolean; onClose: () => void;
  playAudiobook: (id: string) => void;
  playPlaylist: (uri: string) => void;
  playMedia: (query: string, mediaType?: string) => void;
  isDisabled: boolean;
}) => {
  const { trigger } = useHaptics();
  const [tab, setTab] = useState<'ma' | 'abs'>('ma');
  const [search, setSearch] = useState('');
  const [libraryId, setLibraryId] = useState<string | null>(null);
  const [itemLoading, setItemLoading] = useState<string | null>(null);

  const { data: absLibraries, isLoading: absLibrariesLoading } = useQuery({
    queryKey: ['abs-libraries'],
    queryFn: () => api.getAudiobookshelfLibraries(),
    enabled: show && tab === 'abs' && !libraryId,
  });

  const { data: absLibraryItems, isLoading: absLibraryItemsLoading } = useQuery({
    queryKey: ['abs-library-items', libraryId],
    queryFn: () => api.getAudiobookshelfLibrary(libraryId!, 50),
    enabled: show && tab === 'abs' && !!libraryId,
  });

  const { data: absSearchResults, isLoading: absSearchLoading } = useQuery({
    queryKey: ['abs-search', search],
    queryFn: () => api.searchAudiobookshelf(search, 30),
    enabled: show && tab === 'abs' && search.length >= 2,
  });

  const { data: maPlaylists, isLoading: playlistsLoading } = useQuery({
    queryKey: ['ma-playlists'],
    queryFn: () => api.getMusicAssistantPlaylists(),
  });

  const { data: maRecent, isLoading: maRecentLoading } = useQuery({
    queryKey: ['ma-recent'],
    queryFn: () => api.getMusicAssistantRecent(),
  });

  const handlePlay = useCallback(
    (id: string, type: 'audiobook' | 'music' | 'playlist') => {
      if (isDisabled) return;
      trigger('heavy');
      setItemLoading(id);
      try {
        if (type === 'audiobook') playAudiobook(id);
        else if (type === 'playlist') playPlaylist(id);
        else playMedia(id, 'music');
      } finally { setItemLoading(null); }
    },
    [isDisabled, trigger, playAudiobook, playPlaylist, playMedia],
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
                {playlistsLoading ? loadingSection() : !maPlaylists?.playlists?.length ? emptySection('No playlists found') : (
                  <div className="space-y-1.5">
                    {maPlaylists.playlists
                      .filter((pl) => !search || pl.name.toLowerCase().includes(search.toLowerCase()))
                      .map((pl) => (
                        <PlaylistItem key={pl.uri} name={pl.name} trackCount={pl.items}
                          onPlay={() => handlePlay(pl.uri, 'playlist')}
                          isDisabled={isDisabled} isLoading={itemLoading === `pl-${pl.uri}`} />
                      ))}
                  </div>
                )}
              </div>
              <div>
                <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                  <Clock size={12} />Recently Played
                </h3>
                {maRecentLoading ? loadingSection() : !maRecent?.recent?.length ? emptySection('No recent items') : (
                  <div className="space-y-1.5">
                    {maRecent.recent
                      .filter((i) => !search || i.name.toLowerCase().includes(search.toLowerCase()) || i.artist.toLowerCase().includes(search.toLowerCase()))
                      .map((item) => (
                        <button key={item.uri} onClick={() => handlePlay(item.name, 'music')} disabled={isDisabled}
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
                  {absLibrariesLoading ? loadingSection() : !absLibraries?.libraries?.length ? emptySection('No libraries found') : (
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
                  {absLibraryItemsLoading ? loadingSection() : !absLibraryItems?.books?.length ? emptySection('No books in this library') : (
                    <div className="space-y-1.5">
                      {absLibraryItems.books
                        .filter((b) => !search || b.title.toLowerCase().includes(search.toLowerCase()) || b.author.toLowerCase().includes(search.toLowerCase()))
                        .map((book) => (
                          <button key={book.id} onClick={() => handlePlay(book.id, 'audiobook')} disabled={isDisabled}
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
                  {absSearchLoading ? loadingSection() : !absSearchResults?.books?.length ? emptySection(`No results for "${search}"`) : (
                    <div className="space-y-1.5">
                      {absSearchResults.books.map((book) => (
                        <button key={book.id} onClick={() => handlePlay(book.id, 'audiobook')} disabled={isDisabled}
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
  const [selectedTarget, setSelectedTarget] = useState<string>('');
  const [volume, setVolume] = useState(70);
  const [muted, setMuted] = useState(false);
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mediaStatus, setMediaStatus] = useState<MediaStatus | null>(null);
  const [showMediaPicker, setShowMediaPicker] = useState(false);

  const { data: maPlaylists } = useQuery({
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

  const selectedTargetInfo = useMemo(
    () => entities.find((e) => e.entity_id === selectedTarget)
      ? { name: entities.find((e) => e.entity_id === selectedTarget)!.friendly_name, room: entities.find((e) => e.entity_id === selectedTarget)!.entity_id.split('.')[1]?.replace(/_/g, ' ') || 'Unknown' }
      : undefined,
    [entities, selectedTarget],
  );

  const quickResumeItems = useMemo(() => {
    const items: Array<{
      id: string; title: string; subtitle: string; type: 'audiobook' | 'music'; progress?: number;
    }> = [];

    if (absLastPlayed?.books) {
      for (const book of absLastPlayed.books) {
        items.push({
          id: `abs-${book.id}`, title: book.title, subtitle: book.author,
          type: 'audiobook', progress: book.progress,
        });
      }
    }
    if (maRecent?.recent) {
      for (const item of maRecent.recent) {
        items.push({
          id: `ma-${item.uri}`, title: item.name, subtitle: item.artist,
          type: 'music',
        });
      }
    }
    return items;
  }, [absLastPlayed, maRecent]);

  /* ── media status polling ───────────────────────────────────── */

  const fetchMediaStatus = useCallback(async () => {
    try {
      const resp = await api.mediaStatus();
      if (resp.status === 'SUCCESS' && resp.detail) {
        setMediaStatus(resp.detail as MediaStatus);
        if (resp.detail.volume_level !== undefined) setVolume(Math.round(resp.detail.volume_level * 100));
        if (resp.detail.is_volume_muted !== undefined) setMuted(resp.detail.is_volume_muted);
        if (resp.detail.entity_id) setSelectedTarget(resp.detail.entity_id);
      }
    } catch { /* ignore */ }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    fetchMediaStatus();
    const interval = setInterval(fetchMediaStatus, 10000);
    return () => clearInterval(interval);
  }, [fetchMediaStatus]);
  /* eslint-enable react-hooks/set-state-in-effect */

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

  const isDisabled = !selectedTarget;

  return (
    <div className="space-y-6 max-w-4xl mx-auto pb-24">
      <h1 className="text-2xl font-bold text-white">Media</h1>

      {error && (
        <div className="bg-red-500/20 border border-red-500/30 rounded-xl p-3 text-red-400 text-sm flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="ml-3 underline text-xs shrink-0">Dismiss</button>
        </div>
      )}

      {/* 1. Active Player Header */}
       <NowPlayingCard
        mediaStatus={mediaStatus}
        selectedTarget={selectedTarget}
        selectedTargetInfo={selectedTargetInfo}
        volume={volume}
        muted={muted}
        loading={loading}
        entities={entities}
        onPrevious={() => sendTransport('previous')}
        onTogglePlay={() => sendTransport(mediaStatus?.state === 'playing' ? 'pause' : 'play')}
        onNext={() => sendTransport('next')}
        onVolumeChange={handleVolume}
        onMuteToggle={toggleMute}
      />

      {/* 2. Jump Back In */}
      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Jump Back In</h2>
        {(maRecentLoading || absLoading) && quickResumeItems.length === 0 ? (
          loadingSection()
        ) : quickResumeItems.length === 0 ? (
          emptySection('No recently played content')
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {quickResumeItems.map((item) => {
              const id = item.id;
              const handlePlay = item.type === 'audiobook'
                ? () => playAudiobook(item.id.replace('abs-', ''))
                : () => playMedia(item.title, 'music');
              return (
                <QuickResumeItem
                  key={id} item={item}
                  onPlay={handlePlay} isDisabled={isDisabled}
                  isLoading={loading !== null}
                />
              );
            })}
          </div>
        )}
      </section>

      {/* 3. Playlists */}
      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Playlists</h2>
        {!maPlaylists?.playlists?.length ? (
          emptySection('No playlists available')
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {maPlaylists.playlists.map((pl) => (
              <PlaylistItem
                key={pl.uri} name={pl.name} trackCount={pl.items}
                onPlay={() => playPlaylist(pl.uri)}
                isDisabled={isDisabled} isLoading={loading !== null}
              />
            ))}
          </div>
        )}
      </section>

      {/* 4. Browse All Media */}
      <button
        onClick={() => { setShowMediaPicker(true); }}
        disabled={isDisabled}
        className={`w-full py-4 rounded-2xl bg-gradient-to-r from-cyan-500/10 to-purple-500/10 border border-cyan-500/20 text-cyan-400 font-medium hover:from-cyan-500/20 hover:to-purple-500/20 transition-all flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed`}
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
        isDisabled={isDisabled}
      />
    </div>
  );
};

export default Media;
