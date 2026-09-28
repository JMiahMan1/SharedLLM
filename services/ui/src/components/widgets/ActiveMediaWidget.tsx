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
  const [volumeLevel, setVolumeLevel] = useState(50);
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
          setVolumeLevel(Math.round((active.volume_level ?? 0.5) * 100));
        } else {
          setMedia(null);
          setPosition(0);
          setDuration(0);
          setVolumeLevel(50);
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

  // Scrubbing and volume drags fire one request per movement frame, which
  // floods the player with intermediate positions. Keep the knob glued to the
  // pointer locally and let only the settled value reach the device.
  const CONTROL_DEBOUNCE_MS = 200;
  const seekTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const volumeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingSeekRef = useRef<number | null>(null);

  const flushSeek = useCallback(() => {
    if (seekTimerRef.current) {
      clearTimeout(seekTimerRef.current);
      seekTimerRef.current = null;
    }
    const target = pendingSeekRef.current;
    pendingSeekRef.current = null;
    if (target === null || !media?.entity_id) return;
    api
      .mediaTransport({ entity_id: media.entity_id, command: 'seek', position: Math.round(target) })
      .catch(() => toast.error('Could not seek on that player'));
  }, [media]);

  const handleSeek = useCallback((timeSec: number) => {
    const clamped = Math.max(0, duration > 0 ? Math.min(timeSec, duration) : timeSec);
    localTimeRef.current = clamped;
    setPosition(clamped);
    if (!media?.entity_id) return;
    pendingSeekRef.current = clamped;
    if (seekTimerRef.current) clearTimeout(seekTimerRef.current);
    seekTimerRef.current = setTimeout(flushSeek, CONTROL_DEBOUNCE_MS);
  }, [media, duration, flushSeek]);

  const commitVolume = useCallback((entityId: string, level: number) => {
    if (volumeTimerRef.current) clearTimeout(volumeTimerRef.current);
    volumeTimerRef.current = setTimeout(() => {
      volumeTimerRef.current = null;
      api
        .mediaTransport({ entity_id: entityId, command: 'volume_set', volume_level: level / 100 })
        .catch(() => toast.error('Could not set the volume on that player'));
    }, CONTROL_DEBOUNCE_MS);
  }, []);

  // Never leave a pending control request firing into an unmounted widget.
  useEffect(() => () => {
    if (seekTimerRef.current) clearTimeout(seekTimerRef.current);
    if (volumeTimerRef.current) clearTimeout(volumeTimerRef.current);
  }, []);

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
      // Release is the moment the user settled on a position: send that one seek.
      flushSeek();
      document.removeEventListener('pointermove', moveHandler);
      document.removeEventListener('pointerup', upHandler);
      document.removeEventListener('pointercancel', upHandler);
    };
    document.addEventListener('pointermove', moveHandler);
    document.addEventListener('pointerup', upHandler);
    document.addEventListener('pointercancel', upHandler);
  }, [duration, media, handleSeek, flushSeek]);

  // The scrubber advertises role="slider", so it has to be operable without a
  // pointer too. Arrow keys step 5s (30s with Shift).
  const handleScrubberKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (!duration || !media?.entity_id) return;
    const step = e.shiftKey ? 30 : 5;
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') {
      e.preventDefault();
      handleSeek(position + step);
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') {
      e.preventDefault();
      handleSeek(position - step);
    } else if (e.key === 'Home') {
      e.preventDefault();
      handleSeek(0);
    } else if (e.key === 'End') {
      e.preventDefault();
      handleSeek(duration);
    }
  }, [duration, media, handleSeek, position]);

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
              onKeyDown={handleScrubberKeyDown}
              role="slider"
              tabIndex={0}
              aria-label="Track progress scrubber"
              aria-valuemin={0}
              aria-valuemax={Math.round(duration)}
              aria-valuenow={Math.round(position)}
              aria-valuetext={`${formatTime(position)} of ${formatTime(duration)}`}
              className="relative py-4 sm:py-2 select-none touch-none cursor-pointer group"
            >
              <div className="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden relative pointer-events-none">
                <div
                  className="h-full bg-gradient-to-r from-purple-500 to-pink-400 rounded-full transition-[width] duration-300"
                  style={{ width: `${Math.min(100, Math.max(0, progressPercent))}%` }}
                />
              </div>
              <div
                className="absolute top-1/2 -translate-y-1/2 w-4 h-4 sm:w-3 sm:h-3 bg-white rounded-full shadow-sm pointer-events-none -ml-2 sm:-ml-1.5 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity"
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
              value={volumeLevel}
              aria-label="Volume"
              className="flex-1 h-1 bg-slate-800 rounded-full appearance-none cursor-pointer accent-purple-500"
              onChange={(e) => {
                const next = Number(e.target.value);
                setVolumeLevel(next);
                // Local value moves instantly; the device gets one settled call.
                if (media?.entity_id) commitVolume(media.entity_id, next);
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
