import { useEffect, useState, useRef, useCallback } from 'react';
import { Music, Play, Pause, SkipBack, SkipForward, Volume2 } from 'lucide-react';
import type { IActiveMediaWidgetProps, MediaState } from '../../types/widget';
import { api } from '../../services/api';
import toast from 'react-hot-toast';

function formatTime(seconds: number): string {
  const totalSec = Math.max(0, Math.floor(seconds));
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

const ActiveMediaWidget = ({ userSettings, onTogglePin, onMediaStop, settingsButton }: IActiveMediaWidgetProps) => {
  const [media, setMedia] = useState<MediaState | null>(null);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const localTimeRef = useRef(0);

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
              position?: number;
              duration?: number;
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
          if (active.duration && active.duration > 0) {
            setDuration(active.duration);
          }
          if (active.position && active.position > 0) {
            setPosition(active.position);
            localTimeRef.current = active.position;
          }
        } else {
          setMedia(null);
          setPosition(0);
          setDuration(0);
          if (!userSettings.is_pinned) onMediaStop?.();
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

  // Tick local time while playing (HA position/duration are in seconds)
  useEffect(() => {
    let timer: number | null = null;
    if (media?.state === 'playing') {
      timer = window.setInterval(() => {
        localTimeRef.current += 1;
        if (duration > 0 && localTimeRef.current >= duration) {
          localTimeRef.current = duration;
        }
        setPosition(localTimeRef.current);
      }, 1000);
    }
    return () => {
      if (timer) clearInterval(timer);
    };
  }, [media, duration]);

  const handleSeek = useCallback((timeSec: number) => {
    const clamped = Math.max(0, duration > 0 ? Math.min(timeSec, duration) : timeSec);
    localTimeRef.current = clamped;
    setPosition(clamped);
    if (media?.entity_id) {
      try {
        api.mediaTransport({ entity_id: media.entity_id, command: 'seek', position: Math.round(clamped) });
      } catch { /* ignore */ }
    }
  }, [media, duration]);

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

  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    if (!duration || !media?.entity_id) return;
    // Capture the track element now: React clears `currentTarget` once this
    // handler returns, so the document-level drag listeners below cannot read
    // it off the original event.
    const track = e.currentTarget as HTMLElement;

    const calculatePosition = (clientX: number) => {
      const rect = track.getBoundingClientRect();
      if (rect.width === 0) return;
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      handleSeek(ratio * duration);
    };

    calculatePosition(e.clientX);
    const moveHandler = (ev: PointerEvent) => calculatePosition(ev.clientX);
    const upHandler = () => {
      document.removeEventListener('pointermove', moveHandler);
      document.removeEventListener('pointerup', upHandler);
      document.removeEventListener('pointercancel', upHandler);
    };
    document.addEventListener('pointermove', moveHandler);
    document.addEventListener('pointerup', upHandler);
    document.addEventListener('pointercancel', upHandler);
  }, [duration, media, handleSeek]);

  const progressPercent = duration > 0 ? (position / duration) * 100 : 0;

  return (
    <div className="glass-card h-full p-5 relative">
      <div className="absolute top-3 right-3 flex items-center gap-2 z-10">
        <button
          onClick={onTogglePin}
          className="text-slate-500 hover:text-purple-400 transition-colors"
          title={userSettings.is_pinned ? 'Unpin widget' : 'Pin widget'}
        >
          <Music size={16} className={userSettings.is_pinned ? 'text-purple-400' : ''} />
        </button>
        {settingsButton}
      </div>

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

          {duration > 0 && (
            <div
              onPointerDown={handlePointerDown}
              role="slider"
              tabIndex={0}
              aria-label="Track progress scrubber"
              aria-valuemin={0}
              aria-valuemax={Math.round(duration)}
              aria-valuenow={Math.round(position)}
              className="relative py-2 select-none touch-none cursor-pointer group"
            >
              <div className="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden relative pointer-events-none">
                <div
                  className="h-full bg-gradient-to-r from-purple-500 to-pink-400 rounded-full transition-[width] duration-300"
                  style={{ width: `${Math.min(100, Math.max(0, progressPercent))}%` }}
                />
              </div>
              <div
                className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full shadow-sm pointer-events-none -ml-1.5 opacity-0 group-hover:opacity-100 transition-opacity"
                style={{ left: `${Math.min(100, Math.max(0, progressPercent))}%` }}
              />
            </div>
          )}
          {duration > 0 && (
            <p className="text-[10px] text-slate-500 font-mono text-center -mt-2">
              {formatTime(position)} / {formatTime(duration)}
            </p>
          )}

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
