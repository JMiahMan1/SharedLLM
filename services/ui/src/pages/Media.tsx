import { useState, useCallback, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Play, Pause, SkipForward, SkipBack, Volume2, Cast, Search, Music, BookOpen, List, Loader2 } from 'lucide-react';
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

const Media = () => {
  const { trigger } = useHaptics();
  const [selectedTarget, setSelectedTarget] = useState<string>('');
  const [volume, setVolume] = useState(70);
  const [muted, setMuted] = useState(false);
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mediaStatus, setMediaStatus] = useState<MediaStatus | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [showMediaPicker, setShowMediaPicker] = useState(false);
  const [mediaPickerTab, setMediaPickerTab] = useState<'ma' | 'abs'>('ma');

  const { data: entities = [] } = useQuery({
    queryKey: ['media-entities'],
    queryFn: () => api.getEntities(),
    select: (data) => data.filter((e) => e.domain === 'media_player'),
  });

  const { data: maPlaylists } = useQuery({
    queryKey: ['ma-playlists'],
    queryFn: () => api.getMusicAssistantPlaylists(),
    enabled: showMediaPicker && mediaPickerTab === 'ma',
  });

  const { data: maRecent } = useQuery({
    queryKey: ['ma-recent'],
    queryFn: () => api.getMusicAssistantRecent(),
    enabled: showMediaPicker && mediaPickerTab === 'ma',
  });

  const { data: absLastPlayed } = useQuery({
    queryKey: ['abs-last-played'],
    queryFn: () => api.getAudiobookshelfLastPlayed(),
    enabled: showMediaPicker && mediaPickerTab === 'abs',
  });

  const mediaTargets = entities.map((entity) => ({
    id: entity.entity_id,
    name: entity.friendly_name || entity.entity_id,
    room: entity.entity_id.split('.')[1]?.replace(/_/g, ' ') || 'Unknown',
    type: entity.entity_id.includes('tv') ? 'tv' : 'speaker',
    online: entity.state !== 'unavailable' && entity.state !== 'unknown',
  }));

  const filteredTargets = mediaTargets.filter(
    (t) =>
      t.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      t.room.toLowerCase().includes(searchQuery.toLowerCase())
  );

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
      // Ignore status fetch errors
    }
  }, []);

  useEffect(() => {
    fetchMediaStatus();
    const interval = setInterval(fetchMediaStatus, 10000);
    return () => clearInterval(interval);
  }, [fetchMediaStatus]);

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

  const handlePlayMedia = useCallback(async (query: string, mediaType?: string) => {
    if (!selectedTarget) {
      setError('No media player selected');
      return;
    }
    trigger('heavy');
    setLoading('play');
    setError(null);
    try {
      const resp = await api.mediaPlay({
        entity_id: selectedTarget,
        query,
        media_type: mediaType,
      });
      if (resp.status === 'FAILURE') {
        setError(resp.message || 'Playback failed');
      }
      setShowMediaPicker(false);
      await fetchMediaStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Playback failed');
    } finally {
      setLoading(null);
    }
  }, [selectedTarget, trigger, fetchMediaStatus]);

  const handleVolumeChange = useCallback(async (newVolume: number) => {
    if (!selectedTarget) return;
    setVolume(newVolume);
    try {
      await api.mediaTransport({ entity_id: selectedTarget, command: 'volume_set', volume_level: newVolume / 100 });
    } catch {
      // Ignore volume errors
    }
  }, [selectedTarget]);

  const nowPlaying = mediaStatus?.state === 'playing' || mediaStatus?.state === 'paused';

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold text-white">Media</h1>

      {error && (
        <div className="bg-red-500/20 border border-red-500/30 rounded-xl p-3 text-red-400 text-sm">
          {error}
          <button onClick={() => setError(null)} className="ml-2 underline">Dismiss</button>
        </div>
      )}

      <div className="glass-panel rounded-2xl p-4 md:p-6 border border-cyan-500/20">
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4">
          <div className="w-20 h-20 sm:w-24 sm:h-24 rounded-xl bg-gradient-to-br from-cyan-500/30 to-purple-500/30 flex items-center justify-center shrink-0">
            {nowPlaying ? <Music size={32} className="text-cyan-400" /> : <Play size={32} className="text-cyan-400" />}
          </div>
          <div className="flex-1 min-w-0">
            {nowPlaying ? (
              <>
                <p className="text-white font-medium text-lg">{mediaStatus?.media_title || 'Unknown Title'}</p>
                <p className="text-sm text-slate-400">{mediaStatus?.media_artist || 'Unknown Artist'}</p>
                <p className="text-xs text-slate-500">{mediaStatus?.friendly_name || selectedTarget}</p>
              </>
            ) : (
              <>
                <p className="text-white font-medium text-lg">No Active Playback</p>
                <p className="text-sm text-slate-400">Select a target and content to begin</p>
              </>
            )}
          </div>
        </div>

        <div className="flex items-center justify-center gap-6 mt-6">
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
            className="w-14 h-14 rounded-full bg-cyan-500/20 border border-cyan-500/30 flex items-center justify-center text-cyan-400 hover:bg-cyan-500/30 transition-colors disabled:opacity-50"
          >
            {loading === 'play' || loading === 'pause' ? (
              <Loader2 size={22} className="animate-spin" />
            ) : mediaStatus?.state === 'playing' ? (
              <Pause size={24} />
            ) : (
              <Play size={24} />
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

        <div className="flex items-center gap-3 mt-6">
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
          <button
            onClick={() => setShowMediaPicker(true)}
            className="w-full mt-4 py-3 rounded-xl bg-cyan-500/10 border border-cyan-500/30 text-cyan-400 hover:bg-cyan-500/20 transition-colors"
          >
            Select Media to Play
          </button>
        )}
      </div>

      <div className="glass-panel rounded-2xl p-4 md:p-6">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Cast To</h2>

        <div className="relative mb-4">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            placeholder="Search devices..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full glass-input pl-9 h-10 text-sm rounded-xl"
          />
        </div>

        <div className="space-y-2">
          {filteredTargets.map((target) => (
            <button
              key={target.id}
              onClick={() => { trigger('light'); setSelectedTarget(target.id); }}
              className={`w-full flex items-center justify-between p-3 rounded-xl transition-colors ${
                selectedTarget === target.id
                  ? 'bg-cyan-500/20 border border-cyan-500/30'
                  : 'bg-white/5 hover:bg-white/10'
              } ${!target.online ? 'opacity-50' : ''}`}
            >
              <div className="flex items-center gap-3">
                <Cast size={18} className="text-slate-400 shrink-0" />
                <div className="text-left min-w-0">
                  <p className="text-white text-sm font-medium truncate">{target.name}</p>
                  <p className="text-xs text-slate-400">{target.room}</p>
                </div>
              </div>
              <div className={`w-2 h-2 rounded-full shrink-0 ${target.online ? 'bg-green-400' : 'bg-slate-600'}`} />
            </button>
          ))}
          {filteredTargets.length === 0 && mediaTargets.length > 0 && (
            <p className="text-sm text-slate-500 text-center py-4">No devices match "{searchQuery}"</p>
          )}
          {mediaTargets.length === 0 && (
            <p className="text-sm text-slate-500 text-center py-4">No media players found. Check Home Assistant connection.</p>
          )}
        </div>
      </div>

      {showMediaPicker && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-end sm:items-center justify-center p-4">
          <div className="glass-panel rounded-2xl w-full max-w-2xl max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between p-4 border-b border-white/10">
              <h2 className="text-lg font-semibold text-white">Select Media</h2>
              <button onClick={() => setShowMediaPicker(false)} className="text-slate-400 hover:text-white">Close</button>
            </div>

            <div className="flex border-b border-white/10">
              <button
                onClick={() => setMediaPickerTab('ma')}
                className={`flex-1 py-3 text-sm font-medium ${mediaPickerTab === 'ma' ? 'text-cyan-400 border-b-2 border-cyan-400' : 'text-slate-400'}`}
              >
                <Music size={16} className="inline mr-2" />
                Music Assistant
              </button>
              <button
                onClick={() => setMediaPickerTab('abs')}
                className={`flex-1 py-3 text-sm font-medium ${mediaPickerTab === 'abs' ? 'text-cyan-400 border-b-2 border-cyan-400' : 'text-slate-400'}`}
              >
                <BookOpen size={16} className="inline mr-2" />
                Audiobooks
              </button>
            </div>

            <div className="overflow-y-auto flex-1 p-4">
              {mediaPickerTab === 'ma' && (
                <div className="space-y-4">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-2">Playlists</h3>
                    <div className="space-y-2">
                      {maPlaylists?.playlists?.map((pl) => (
                        <button
                          key={pl.uri}
                          onClick={() => handlePlayMedia(pl.name, 'playlist')}
                          className="w-full flex items-center gap-3 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left"
                        >
                          <List size={18} className="text-cyan-400 shrink-0" />
                          <div className="min-w-0">
                            <p className="text-white text-sm font-medium truncate">{pl.name}</p>
                            <p className="text-xs text-slate-400">{pl.items} tracks</p>
                          </div>
                        </button>
                      ))}
                      {!maPlaylists?.playlists?.length && (
                        <p className="text-sm text-slate-500">No playlists found</p>
                      )}
                    </div>
                  </div>

                  <div>
                    <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-2">Recently Played</h3>
                    <div className="space-y-2">
                      {maRecent?.recent?.map((item) => (
                        <button
                          key={item.uri}
                          onClick={() => handlePlayMedia(item.name, 'music')}
                          className="w-full flex items-center gap-3 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left"
                        >
                          <Music size={18} className="text-purple-400 shrink-0" />
                          <div className="min-w-0">
                            <p className="text-white text-sm font-medium truncate">{item.name}</p>
                            <p className="text-xs text-slate-400">{item.artist}</p>
                          </div>
                        </button>
                      ))}
                      {!maRecent?.recent?.length && (
                        <p className="text-sm text-slate-500">No recent items</p>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {mediaPickerTab === 'abs' && (
                <div className="space-y-2">
                  {absLastPlayed?.books?.map((book) => (
                    <button
                      key={book.id}
                      onClick={() => handlePlayMedia(book.id, 'audiobook')}
                      className="w-full flex items-center gap-3 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors text-left"
                    >
                      <BookOpen size={18} className="text-amber-400 shrink-0" />
                      <div className="min-w-0 flex-1">
                        <p className="text-white text-sm font-medium truncate">{book.title}</p>
                        <p className="text-xs text-slate-400">{book.author}</p>
                        <div className="flex items-center gap-2 mt-1">
                          <div className="flex-1 h-1 bg-white/10 rounded-full overflow-hidden">
                            <div className="h-full bg-amber-400 rounded-full" style={{ width: `${book.progress * 100}%` }} />
                          </div>
                          <span className="text-xs text-slate-500">{Math.round(book.progress * 100)}%</span>
                        </div>
                      </div>
                    </button>
                  ))}
                  {!absLastPlayed?.books?.length && (
                    <p className="text-sm text-slate-500">No recently played audiobooks</p>
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
