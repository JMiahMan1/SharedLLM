import { useState, useCallback, useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Play, Pause, SkipForward, SkipBack, Volume2, Cast, Search,
  Music, BookOpen, List, Loader2, ChevronDown, X, Library,
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

interface MediaTarget {
  id: string;
  name: string;
  room: string;
  type: 'tv' | 'speaker';
  online: boolean;
}

const Media = () => {
  const { trigger } = useHaptics();
  const [selectedTarget, setSelectedTarget] = useState<string>('');
  const [volume, setVolume] = useState(70);
  const [muted, setMuted] = useState(false);
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mediaStatus, setMediaStatus] = useState<MediaStatus | null>(null);
  const [showDeviceDropdown, setShowDeviceDropdown] = useState(false);
  const [showMediaPicker, setShowMediaPicker] = useState(false);
  const [mediaPickerTab, setMediaPickerTab] = useState<'ma' | 'abs'>('ma');
  const [modalSearch, setModalSearch] = useState('');
  const [selectedLibraryId, setSelectedLibraryId] = useState<string | null>(null);
  const [itemLoading, setItemLoading] = useState<string | null>(null);

  const { data: entities = [] } = useQuery({
    queryKey: ['media-entities'],
    queryFn: () => api.getEntities(),
    select: (data) => data.filter((e) => e.domain === 'media_player'),
  });

  const { data: maPlaylists, isLoading: playlistsLoading } = useQuery({
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

  const { data: absLibraries, isLoading: absLibrariesLoading } = useQuery({
    queryKey: ['abs-libraries'],
    queryFn: () => api.getAudiobookshelfLibraries(),
    enabled: showMediaPicker && mediaPickerTab === 'abs' && !selectedLibraryId,
  });

  const { data: absLibraryItems, isLoading: absLibraryItemsLoading } = useQuery({
    queryKey: ['abs-library-items', selectedLibraryId],
    queryFn: () => api.getAudiobookshelfLibrary(selectedLibraryId!, 50),
    enabled: showMediaPicker && mediaPickerTab === 'abs' && !!selectedLibraryId,
  });

  const { data: absSearchResults, isLoading: absSearchLoading } = useQuery({
    queryKey: ['abs-search', modalSearch],
    queryFn: () => api.searchAudiobookshelf(modalSearch, 30),
    enabled: showMediaPicker && mediaPickerTab === 'abs' && modalSearch.length >= 2,
  });

  const mediaTargets: MediaTarget[] = useMemo(
    () =>
      entities.map((entity) => ({
        id: entity.entity_id,
        name: entity.friendly_name || entity.entity_id,
        room: entity.entity_id.split('.')[1]?.replace(/_/g, ' ') || 'Unknown',
        type: entity.entity_id.includes('tv') ? 'tv' : 'speaker',
        online: entity.state !== 'unavailable' && entity.state !== 'unknown',
      })),
    [entities],
  );

  const selectedTargetInfo = useMemo(
    () => mediaTargets.find((t) => t.id === selectedTarget),
    [mediaTargets, selectedTarget],
  );

  const quickResumeItems = useMemo(() => {
    const items: Array<{
      id: string;
      title: string;
      subtitle: string;
      type: 'audiobook' | 'music';
      source: string;
      progress?: number;
    }> = [];

    if (absLastPlayed?.books) {
      for (const book of absLastPlayed.books) {
        items.push({
          id: `abs-${book.id}`,
          title: book.title,
          subtitle: book.author,
          type: 'audiobook',
          source: 'ABS',
          progress: book.progress,
        });
      }
    }

    if (maRecent?.recent) {
      for (const item of maRecent.recent) {
        items.push({
          id: `ma-${item.uri}`,
          title: item.name,
          subtitle: item.artist,
          type: 'music',
          source: 'MA',
        });
      }
    }

    return items;
  }, [absLastPlayed, maRecent]);

  const fetchMediaStatus = useCallback(async () => {
    try {
      const resp = await api.mediaStatus();
      if (resp.status === 'SUCCESS' && resp.detail) {
        setMediaStatus(resp.detail as MediaStatus);
        if (resp.detail.volume_level !== undefined) {
          setVolume(Math.round(resp.detail.volume_level * 100));
        }
        if (resp.detail.is_volume_muted !== undefined) {
          setMuted(resp.detail.is_volume_muted);
        }
        if (resp.detail.entity_id) {
          setSelectedTarget(resp.detail.entity_id);
        }
      }
    } catch {
      // Ignore
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchMediaStatus();
    const interval = setInterval(fetchMediaStatus, 10000);
    return () => clearInterval(interval);
  }, [fetchMediaStatus]);

  useEffect(() => {
    if (!showDeviceDropdown) return;
    const handler = () => setShowDeviceDropdown(false);
    const timer = setTimeout(() => document.addEventListener('click', handler), 0);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('click', handler);
    };
  }, [showDeviceDropdown]);

  const sendTransport = useCallback(async (command: string) => {
    if (!selectedTarget) {
      setError('No media player selected');
      return;
    }
    trigger('light');
    setLoading(command);
    setError(null);
    try {
      const resp = await api.mediaTransport({ entity_id: selectedTarget, command });
      if (resp.status === 'FAILURE') {
        setError(resp.message || 'Command failed');
      }
      await fetchMediaStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Command failed');
    } finally {
      setLoading(null);
    }
  }, [selectedTarget, trigger, fetchMediaStatus]);

  const playMedia = useCallback(
    async (query: string, mediaType?: string) => {
      if (!selectedTarget) {
        setError('Select a device first');
        return;
      }
      trigger('heavy');
      setItemLoading(`play-${query}`);
      setError(null);
      try {
        const resp = await api.mediaPlay({
          entity_id: selectedTarget,
          query,
          media_type: mediaType,
        });
        if (resp.status === 'FAILURE') {
          setError(resp.message || 'Playback failed');
        } else {
          setShowMediaPicker(false);
          await fetchMediaStatus();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Playback failed');
      } finally {
        setItemLoading(null);
      }
    },
    [selectedTarget, trigger, fetchMediaStatus],
  );

  const playAudiobook = useCallback(
    async (bookId: string) => {
      if (!selectedTarget) {
        setError('Select a device first');
        return;
      }
      trigger('heavy');
      setItemLoading(`abs-${bookId}`);
      setError(null);
      try {
        const resp = await api.playAudiobook({
          book_id: bookId,
          entity_id: selectedTarget,
          resume: true,
        });
        if (resp.status === 'FAILURE') {
          setError(resp.message || 'Playback failed');
        } else {
          setShowMediaPicker(false);
          await fetchMediaStatus();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Playback failed');
      } finally {
        setItemLoading(null);
      }
    },
    [selectedTarget, trigger, fetchMediaStatus],
  );

  const playPlaylist = useCallback(
    async (uri: string) => {
      if (!selectedTarget) {
        setError('Select a device first');
        return;
      }
      trigger('heavy');
      setItemLoading(`pl-${uri}`);
      setError(null);
      try {
        const resp = await api.playPlaylist({
          playlist_uri: uri,
          entity_id: selectedTarget,
        });
        if (resp.status === 'FAILURE') {
          setError(resp.message || 'Playback failed');
        } else {
          setShowMediaPicker(false);
          await fetchMediaStatus();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Playback failed');
      } finally {
        setItemLoading(null);
      }
    },
    [selectedTarget, trigger, fetchMediaStatus],
  );

  const handleVolumeChange = useCallback(
    async (newVolume: number) => {
      if (!selectedTarget) return;
      setVolume(newVolume);
      try {
        await api.mediaTransport({ entity_id: selectedTarget, command: 'volume_set', volume_level: newVolume / 100 });
      } catch {
        // Ignore
      }
    },
    [selectedTarget],
  );

  const nowPlaying = mediaStatus?.state === 'playing' || mediaStatus?.state === 'paused';

  return (
    <div className="space-y-6 max-w-4xl mx-auto pb-24">
      <h1 className="text-2xl font-bold text-white">Media</h1>

      {error && (
        <div className="bg-red-500/20 border border-red-500/30 rounded-xl p-3 text-red-400 text-sm flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="ml-3 underline text-xs shrink-0">Dismiss</button>
        </div>
      )}

      {/* Active Player Header */}
      <div className="glass-panel rounded-2xl p-4 md:p-6 border border-cyan-500/20">
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4">
          <div className="w-16 h-16 sm:w-20 sm:h-20 rounded-xl bg-gradient-to-br from-cyan-500/30 to-purple-500/30 flex items-center justify-center shrink-0">
            {nowPlaying ? <Music size={28} className="text-cyan-400" /> : <Play size={28} className="text-cyan-400" />}
          </div>
          <div className="flex-1 min-w-0">
            {nowPlaying ? (
              <>
                <p className="text-white font-medium text-lg truncate">{mediaStatus?.media_title || 'Unknown Title'}</p>
                <p className="text-sm text-slate-400 truncate">{mediaStatus?.media_artist || 'Unknown Artist'}</p>
              </>
            ) : (
              <>
                <p className="text-white font-medium text-lg">No Active Playback</p>
                <p className="text-sm text-slate-400">Select a device and content to begin</p>
              </>
            )}
          </div>

          {/* Device Selector Dropdown */}
          <div className="relative shrink-0">
            <button
              onClick={(e) => { e.stopPropagation(); setShowDeviceDropdown(!showDeviceDropdown); }}
              className={`flex items-center gap-2 px-3 py-2 rounded-xl border text-sm transition-colors ${
                selectedTarget
                  ? 'bg-cyan-500/15 border-cyan-500/30 text-cyan-400'
                  : 'bg-white/5 border-white/10 text-slate-400 hover:border-white/20'
              }`}
            >
              <Cast size={14} />
              <span className="max-w-32 truncate">
                {selectedTargetInfo?.name || 'Cast To'}
              </span>
              <ChevronDown size={14} className="opacity-60" />
            </button>

            {showDeviceDropdown && (
              <div
                className="absolute right-0 top-full mt-2 w-72 glass-panel rounded-xl border border-white/10 shadow-2xl z-50 overflow-hidden"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="p-2 max-h-64 overflow-y-auto custom-scrollbar">
                  {mediaTargets.map((target) => (
                    <button
                      key={target.id}
                      onClick={() => { trigger('light'); setSelectedTarget(target.id); setShowDeviceDropdown(false); }}
                      className={`w-full flex items-center gap-3 p-2.5 rounded-lg transition-colors text-left ${
                        selectedTarget === target.id
                          ? 'bg-cyan-500/20 border border-cyan-500/30'
                          : 'hover:bg-white/10'
                      } ${!target.online ? 'opacity-40' : ''}`}
                    >
                      <Cast size={16} className="text-slate-400 shrink-0" />
                      <div className="min-w-0 flex-1">
                        <p className="text-white text-sm font-medium truncate">{target.name}</p>
                        <p className="text-xs text-slate-500">{target.room}</p>
                      </div>
                      <div className={`w-2 h-2 rounded-full shrink-0 ${target.online ? 'bg-green-400' : 'bg-slate-600'}`} />
                    </button>
                  ))}
                  {mediaTargets.length === 0 && (
                    <p className="text-xs text-slate-500 text-center py-4">No media players found</p>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Transport Controls */}
        <div className="flex items-center justify-center gap-6 mt-5">
          <button
            onClick={() => sendTransport('previous')}
            disabled={loading !== null}
            className="text-slate-400 hover:text-white transition-colors p-2 disabled:opacity-50"
          >
            {loading === 'previous' ? <Loader2 size={22} className="animate-spin" /> : <SkipBack size={22} />}
          </button>
          <button
            onClick={() => sendTransport(mediaStatus?.state === 'playing' ? 'pause' : 'play')}
            disabled={loading !== null}
            className="w-12 h-12 rounded-full bg-cyan-500/20 border border-cyan-500/30 flex items-center justify-center text-cyan-400 hover:bg-cyan-500/30 transition-colors disabled:opacity-50"
          >
            {loading === 'play' || loading === 'pause' ? (
              <Loader2 size={20} className="animate-spin" />
            ) : mediaStatus?.state === 'playing' ? (
              <Pause size={22} />
            ) : (
              <Play size={22} />
            )}
          </button>
          <button
            onClick={() => sendTransport('next')}
            disabled={loading !== null}
            className="text-slate-400 hover:text-white transition-colors p-2 disabled:opacity-50"
          >
            {loading === 'next' ? <Loader2 size={22} className="animate-spin" /> : <SkipForward size={22} />}
          </button>
        </div>

        {/* Volume */}
        <div className="flex items-center gap-3 mt-5">
          <Volume2 size={16} className="text-slate-400 shrink-0" />
          <input
            type="range"
            min="0"
            max="100"
            value={muted ? 0 : volume}
            onChange={(e) => handleVolumeChange(Number(e.target.value))}
            className="flex-1 accent-cyan-400"
          />
          <span className="text-xs text-slate-400 w-8 text-right">{muted ? 'M' : `${volume}%`}</span>
        </div>

        {!selectedTarget && (
          <div className="mt-4 py-2.5 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-400 text-sm text-center">
            Select a device above to enable playback
          </div>
        )}
      </div>

      {/* Jump Back In */}
      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Jump Back In</h2>
        {(maRecentLoading || absLoading) && quickResumeItems.length === 0 ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 size={24} className="text-slate-500 animate-spin" />
          </div>
        ) : quickResumeItems.length === 0 ? (
          <div className="glass-panel rounded-2xl p-8 text-center text-slate-500 text-sm">
            No recently played content
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {quickResumeItems.map((item) => {
              const isLoading = itemLoading === item.id;
              const handlePlay = item.type === 'audiobook'
                ? () => playAudiobook(item.id.replace('abs-', ''))
                : () => playMedia(item.title, 'music');
              return (
                <button
                  key={item.id}
                  onClick={handlePlay}
                  disabled={!selectedTarget || !!itemLoading}
                  className="glass-panel rounded-xl p-3 flex items-center gap-3 text-left transition-all hover:bg-white/10 disabled:opacity-50 disabled:cursor-not-allowed group"
                >
                  <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
                    item.type === 'audiobook'
                      ? 'bg-amber-500/20 text-amber-400'
                      : 'bg-purple-500/20 text-purple-400'
                  }`}>
                    {isLoading ? (
                      <Loader2 size={18} className="animate-spin" />
                    ) : item.type === 'audiobook' ? (
                      <BookOpen size={18} />
                    ) : (
                      <Music size={18} />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-white text-sm font-medium truncate">{item.title}</p>
                    <p className="text-xs text-slate-400 truncate">{item.subtitle}</p>
                    {item.type === 'audiobook' && item.progress !== undefined && (
                      <div className="flex items-center gap-2 mt-1.5">
                        <div className="flex-1 h-1 bg-white/10 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-amber-400 rounded-full transition-all"
                            style={{ width: `${Math.round(item.progress * 100)}%` }}
                          />
                        </div>
                        <span className="text-xs text-slate-500 shrink-0">{Math.round(item.progress * 100)}%</span>
                      </div>
                    )}
                  </div>
                  <Play size={16} className="text-slate-500 opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
                </button>
              );
            })}
          </div>
        )}
      </section>

      {/* Playlists */}
      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Playlists</h2>
        {playlistsLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 size={24} className="text-slate-500 animate-spin" />
          </div>
        ) : !maPlaylists?.playlists?.length ? (
          <div className="glass-panel rounded-2xl p-8 text-center text-slate-500 text-sm">
            No playlists available
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {maPlaylists.playlists.map((pl) => {
              const isLoading = itemLoading === `pl-${pl.uri}`;
              return (
                <button
                  key={pl.uri}
                  onClick={() => playPlaylist(pl.uri)}
                  disabled={!selectedTarget || !!itemLoading}
                  className="glass-panel rounded-xl p-3 flex items-center gap-3 text-left transition-all hover:bg-white/10 disabled:opacity-50 disabled:cursor-not-allowed group"
                >
                  <div className="w-10 h-10 rounded-lg bg-cyan-500/20 text-cyan-400 flex items-center justify-center shrink-0">
                    {isLoading ? <Loader2 size={18} className="animate-spin" /> : <List size={18} />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-white text-sm font-medium truncate">{pl.name}</p>
                    <p className="text-xs text-slate-400">{pl.items} tracks</p>
                  </div>
                  <Play size={16} className="text-slate-500 opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
                </button>
              );
            })}
          </div>
        )}
      </section>

      {/* Browse All Media Button */}
      <button
        onClick={() => { setShowMediaPicker(true); setModalSearch(''); setSelectedLibraryId(null); setMediaPickerTab('ma'); }}
        className="w-full py-4 rounded-2xl bg-gradient-to-r from-cyan-500/10 to-purple-500/10 border border-cyan-500/20 text-cyan-400 font-medium hover:from-cyan-500/20 hover:to-purple-500/20 transition-all flex items-center justify-center gap-2"
      >
        <Library size={20} />
        Browse All Media
      </button>

      {/* Explorer Modal */}
      {showMediaPicker && (
        <div
          className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-end sm:items-center justify-center"
          onClick={() => setShowMediaPicker(false)}
        >
          <div
            className="glass-panel w-full sm:max-w-3xl h-[90vh] sm:h-[80vh] sm:rounded-2xl rounded-t-2xl flex flex-col overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div className="flex items-center justify-between p-4 border-b border-white/10 shrink-0">
              <h2 className="text-lg font-semibold text-white">Browse Media</h2>
              <button
                onClick={() => { setShowMediaPicker(false); setModalSearch(''); setSelectedLibraryId(null); }}
                className="text-slate-400 hover:text-white p-1"
              >
                <X size={20} />
              </button>
            </div>

            {/* Tabs */}
            <div className="flex border-b border-white/10 shrink-0">
              <button
                onClick={() => { setMediaPickerTab('ma'); setSelectedLibraryId(null); setModalSearch(''); }}
                className={`flex-1 py-3 text-sm font-medium flex items-center justify-center gap-2 transition-colors ${
                  mediaPickerTab === 'ma'
                    ? 'text-cyan-400 border-b-2 border-cyan-400'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                <Music size={16} />
                Music Assistant
              </button>
              <button
                onClick={() => { setMediaPickerTab('abs'); setSelectedLibraryId(null); setModalSearch(''); }}
                className={`flex-1 py-3 text-sm font-medium flex items-center justify-center gap-2 transition-colors ${
                  mediaPickerTab === 'abs'
                    ? 'text-cyan-400 border-b-2 border-cyan-400'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                <BookOpen size={16} />
                Audiobooks
              </button>
            </div>

            {/* Sticky Search Bar */}
            <div className="p-3 border-b border-white/5 shrink-0">
              <div className="relative">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  type="text"
                  placeholder={mediaPickerTab === 'abs' ? 'Search audiobooks...' : 'Filter...'}
                  value={modalSearch}
                  onChange={(e) => setModalSearch(e.target.value)}
                  className="w-full glass-input pl-9 h-10 text-sm rounded-xl"
                />
                {modalSearch && (
                  <button
                    onClick={() => setModalSearch('')}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white"
                  >
                    <X size={14} />
                  </button>
                )}
              </div>
            </div>

            {/* Content Area */}
            <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
              {/* Music Assistant Tab */}
              {mediaPickerTab === 'ma' && (
                <div className="space-y-6">
                  {/* MA Playlists */}
                  <div>
                    <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Playlists</h3>
                    {playlistsLoading ? (
                      <div className="flex justify-center py-4"><Loader2 size={20} className="text-slate-500 animate-spin" /></div>
                    ) : !maPlaylists?.playlists?.length ? (
                      <p className="text-sm text-slate-500 py-2">No playlists found</p>
                    ) : (
                      <div className="space-y-1.5">
                        {maPlaylists.playlists
                          .filter((pl) => !modalSearch || pl.name.toLowerCase().includes(modalSearch.toLowerCase()))
                          .map((pl) => {
                            const isLoading = itemLoading === `pl-${pl.uri}`;
                            return (
                              <button
                                key={pl.uri}
                                onClick={() => playPlaylist(pl.uri)}
                                disabled={!selectedTarget || !!itemLoading}
                                className="w-full flex items-center gap-3 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left disabled:opacity-50"
                              >
                                {isLoading ? (
                                  <Loader2 size={18} className="text-cyan-400 animate-spin shrink-0" />
                                ) : (
                                  <List size={18} className="text-cyan-400 shrink-0" />
                                )}
                                <div className="min-w-0 flex-1">
                                  <p className="text-white text-sm font-medium truncate">{pl.name}</p>
                                  <p className="text-xs text-slate-400">{pl.items} tracks</p>
                                </div>
                              </button>
                            );
                          })}
                      </div>
                    )}
                  </div>

                  {/* MA Recent */}
                  <div>
                    <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Recently Played</h3>
                    {maRecentLoading ? (
                      <div className="flex justify-center py-4"><Loader2 size={20} className="text-slate-500 animate-spin" /></div>
                    ) : !maRecent?.recent?.length ? (
                      <p className="text-sm text-slate-500 py-2">No recent items</p>
                    ) : (
                      <div className="space-y-1.5">
                        {maRecent.recent
                          .filter(
                            (item) =>
                              !modalSearch ||
                              item.name.toLowerCase().includes(modalSearch.toLowerCase()) ||
                              item.artist.toLowerCase().includes(modalSearch.toLowerCase()),
                          )
                          .map((item) => {
                            const isLoading = itemLoading === `ma-${item.uri}`;
                            return (
                              <button
                                key={item.uri}
                                onClick={() => playMedia(item.name, 'music')}
                                disabled={!selectedTarget || !!itemLoading}
                                className="w-full flex items-center gap-3 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left disabled:opacity-50"
                              >
                                {isLoading ? (
                                  <Loader2 size={18} className="text-purple-400 animate-spin shrink-0" />
                                ) : (
                                  <Music size={18} className="text-purple-400 shrink-0" />
                                )}
                                <div className="min-w-0 flex-1">
                                  <p className="text-white text-sm font-medium truncate">{item.name}</p>
                                  <p className="text-xs text-slate-400">{item.artist}</p>
                                </div>
                              </button>
                            );
                          })}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Audiobookshelf Tab */}
              {mediaPickerTab === 'abs' && (
                <div>
                  {/* Library breadcrumb */}
                  {selectedLibraryId && absLibraries && (
                    <button
                      onClick={() => { setSelectedLibraryId(null); setModalSearch(''); }}
                      className="text-sm text-cyan-400 hover:text-cyan-300 mb-3 flex items-center gap-1"
                    >
                      <ChevronDown size={14} className="rotate-90" />
                      Back to Libraries
                    </button>
                  )}

                  {/* Library List */}
                  {!selectedLibraryId && (
                    <div>
                      <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Libraries</h3>
                      {absLibrariesLoading ? (
                        <div className="flex justify-center py-4"><Loader2 size={20} className="text-slate-500 animate-spin" /></div>
                      ) : !absLibraries?.libraries?.length ? (
                        <p className="text-sm text-slate-500 py-2">No libraries found</p>
                      ) : (
                        <div className="space-y-1.5">
                          {absLibraries.libraries
                            .filter((lib) => !modalSearch || lib.name.toLowerCase().includes(modalSearch.toLowerCase()))
                            .map((lib) => (
                              <button
                                key={lib.id}
                                onClick={() => setSelectedLibraryId(lib.id)}
                                className="w-full flex items-center gap-3 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left"
                              >
                                <Library size={18} className="text-amber-400 shrink-0" />
                                <div className="min-w-0 flex-1">
                                  <p className="text-white text-sm font-medium truncate">{lib.name}</p>
                                  <p className="text-xs text-slate-400 capitalize">{lib.media_type}</p>
                                </div>
                              </button>
                            ))}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Library Items */}
                  {selectedLibraryId && (
                    <div>
                      <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
                        {absLibraryItems?.status ? 'Browse' : 'Loading...'}
                      </h3>
                      {absLibraryItemsLoading ? (
                        <div className="flex justify-center py-4"><Loader2 size={20} className="text-slate-500 animate-spin" /></div>
                      ) : !absLibraryItems?.books?.length ? (
                        <p className="text-sm text-slate-500 py-2">No books in this library</p>
                      ) : (
                        <div className="space-y-1.5">
                          {absLibraryItems.books
                            .filter(
                              (book) =>
                                !modalSearch ||
                                book.title.toLowerCase().includes(modalSearch.toLowerCase()) ||
                                book.author.toLowerCase().includes(modalSearch.toLowerCase()),
                            )
                            .map((book) => {
                              const isLoading = itemLoading === `abs-${book.id}`;
                              return (
                                <button
                                  key={book.id}
                                  onClick={() => playAudiobook(book.id)}
                                  disabled={!selectedTarget || !!itemLoading}
                                  className="w-full flex items-center gap-3 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left disabled:opacity-50"
                                >
                                  {isLoading ? (
                                    <Loader2 size={18} className="text-amber-400 animate-spin shrink-0" />
                                  ) : (
                                    <BookOpen size={18} className="text-amber-400 shrink-0" />
                                  )}
                                  <div className="min-w-0 flex-1">
                                    <p className="text-white text-sm font-medium truncate">{book.title}</p>
                                    <p className="text-xs text-slate-400">{book.author}</p>
                                  </div>
                                </button>
                              );
                            })}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Search Results (when typing) */}
                  {modalSearch.length >= 2 && (
                    <div className="mt-4">
                      <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
                        Search Results
                      </h3>
                      {absSearchLoading ? (
                        <div className="flex justify-center py-4"><Loader2 size={20} className="text-slate-500 animate-spin" /></div>
                      ) : !absSearchResults?.books?.length ? (
                        <p className="text-sm text-slate-500 py-2">No results for &quot;{modalSearch}&quot;</p>
                      ) : (
                        <div className="space-y-1.5">
                          {absSearchResults.books.map((book) => {
                            const isLoading = itemLoading === `abs-${book.id}`;
                            return (
                              <button
                                key={book.id}
                                onClick={() => playAudiobook(book.id)}
                                disabled={!selectedTarget || !!itemLoading}
                                className="w-full flex items-center gap-3 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left disabled:opacity-50"
                              >
                                {isLoading ? (
                                  <Loader2 size={18} className="text-amber-400 animate-spin shrink-0" />
                                ) : (
                                  <BookOpen size={18} className="text-amber-400 shrink-0" />
                                )}
                                <div className="min-w-0 flex-1">
                                  <p className="text-white text-sm font-medium truncate">{book.title}</p>
                                  <p className="text-xs text-slate-400">{book.author}</p>
                                </div>
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Media;
