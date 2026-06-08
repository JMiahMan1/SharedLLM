import { useEffect, useState } from 'react';
import { Music, Play, Pause, SkipBack, SkipForward, Volume2 } from 'lucide-react';
import type { UserWidgetSettings, MediaState } from '../../types/widget';
import { api } from '../../services/api';
import toast from 'react-hot-toast';

interface ActiveMediaWidgetProps {
  userSettings: UserWidgetSettings;
  onTogglePin: () => void;
  onMediaStop: () => void;
}

const ActiveMediaWidget = ({ userSettings, onTogglePin, onMediaStop }: ActiveMediaWidgetProps) => {
  const [media, setMedia] = useState<MediaState | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const fetchMedia = async () => {
      try {
        const resp = await api.mediaStatus() as {
          status: string;
          detail?: {
            active?: {
              entity_id?: string;
              friendly_name?: string;
              state?: string;
              media_title?: string;
              media_artist?: string;
              media_album?: string;
              volume_level?: number;
              is_volume_muted?: boolean;
            } | null;
          };
        };
        if (resp.status === 'SUCCESS' && resp.detail?.active) {
          const active = resp.detail.active;
          setMedia({
            entity_id: active.entity_id || '',
            device_name: active.friendly_name || active.entity_id || '',
            title: active.media_title || 'Unknown',
            artist: active.media_artist || 'Unknown Artist',
            album: active.media_album || '',
            state: active.state || 'idle',
          });
        } else {
          setMedia(null);
          if (!userSettings.is_pinned) onMediaStop();
        }
      } catch {
        setMedia(null);
      } finally {
        setIsLoading(false);
      }
    };

    fetchMedia();
    const interval = setInterval(fetchMedia, 5000);
    return () => clearInterval(interval);
  }, [onMediaStop, userSettings.is_pinned]);

  const playPause = async () => {
    if (!media?.entity_id) return;
    try {
      await api.mediaTransport({
        entity_id: media.entity_id,
        command: media.state === 'playing' ? 'pause' : 'play',
      });
      setMedia((prev) => prev ? { ...prev, state: prev.state === 'playing' ? 'paused' : 'playing' } : prev);
    } catch {
      toast.error('Failed to control playback');
    }
  };

  return (
    <div className="glass-card h-full p-5 relative">
      <button
        onClick={onTogglePin}
        className="absolute top-3 right-3 text-slate-500 hover:text-purple-400 transition-colors"
        title={userSettings.is_pinned ? 'Unpin widget' : 'Pin widget'}
      >
        <Music size={16} className={userSettings.is_pinned ? 'text-purple-400' : ''} />
      </button>

      {isLoading ? (
        <div className="flex items-center justify-center h-32">
          <p className="text-sm text-slate-500 animate-pulse">Loading media...</p>
        </div>
      ) : media ? (
        <div className="space-y-4">
          <div className="flex items-center gap-4">
            <div className="w-16 h-16 rounded-xl bg-gradient-to-br from-purple-500/20 to-pink-500/20 border border-purple-500/30 flex items-center justify-center shrink-0">
              <Music size={24} className="text-purple-400" />
            </div>
            <div className="min-w-0">
              <p className="font-bold text-white truncate">{media.title || 'Unknown'}</p>
              <p className="text-sm text-slate-400 truncate">{media.artist || 'Unknown Artist'}</p>
              <p className="text-xs text-slate-500 truncate">{media.device_name}</p>
            </div>
          </div>

          <div className="flex items-center justify-center gap-4">
            <button
              onClick={async () => {
                if (media?.entity_id) {
                  try { await api.mediaTransport({ entity_id: media.entity_id, command: 'previous' }); } catch { /* ignore */ }
                }
              }}
              className="text-slate-400 hover:text-white transition-colors"
            >
              <SkipBack size={20} />
            </button>
            <button
              onClick={playPause}
              className="w-12 h-12 rounded-full bg-purple-500/30 border border-purple-500/40 flex items-center justify-center text-purple-400 hover:bg-purple-500/40 transition-colors"
            >
              {media.state === 'playing' ? <Pause size={20} /> : <Play size={20} />}
            </button>
            <button
              onClick={async () => {
                if (media?.entity_id) {
                  try { await api.mediaTransport({ entity_id: media.entity_id, command: 'next' }); } catch { /* ignore */ }
                }
              }}
              className="text-slate-400 hover:text-white transition-colors"
            >
              <SkipForward size={20} />
            </button>
          </div>

          <div className="flex items-center gap-2">
            <Volume2 size={16} className="text-slate-500" />
            <input
              type="range"
              min={0}
              max={100}
              defaultValue={50}
              className="flex-1 h-1 bg-slate-800 rounded-full appearance-none cursor-pointer accent-purple-500"
              onChange={async (e) => {
                if (media?.entity_id) {
                  try { await api.mediaTransport({ entity_id: media.entity_id, command: 'volume_set', volume_level: Number(e.target.value) / 100 }); } catch { /* ignore */ }
                }
              }}
            />
          </div>
        </div>
      ) : (
        <div className="flex items-center justify-center h-32">
          <p className="text-sm text-slate-500">No active media</p>
        </div>
      )}
    </div>
  );
};

export default ActiveMediaWidget;
