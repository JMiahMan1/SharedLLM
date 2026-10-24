import { useEffect, useState, useRef, useCallback } from 'react';
import { Music, Play, Pause, SkipBack, SkipForward, Volume2 } from 'lucide-react';
import type { IActiveMediaWidgetProps, MediaState } from '../../types/widget';
import { api } from '../../services/api';
import { formatDuration } from '../../features/media/format';
import toast from 'react-hot-toast';
import { useOptionalWebPlayer } from '../../context/webPlayerContext';

/** The entity id the execution service reports for this app's own web player. */
const WEB_PLAYER_ENTITY = 'web_player';

type TransportRequest = Parameters<typeof api.mediaTransport>[0];

const ActiveMediaWidget = ({ userSettings, onMediaStop, settingsButton }: IActiveMediaWidgetProps) => {
  const [media, setMedia] = useState<MediaState | null>(null);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volumeLevel, setVolumeLevel] = useState(50);
  const [isLoading, setIsLoading] = useState(true);
  const localTimeRef = useRef(0);
  // The context value changes on every player tick; read it through a ref so
  // the debounced seek/volume callbacks below stay stable.
  const webPlayer = useOptionalWebPlayer();
  const webPlayerRef = useRef(webPlayer);
  useEffect(() => {
    webPlayerRef.current = webPlayer;
  }, [webPlayer]);

  // The server only stores the web player's last report; sending it a transport
  // command changes that record while the audio on this device plays on. When
  // the active player is this app's own, drive it directly.
  const transport = useCallback(async (req: TransportRequest) => {
    const webPlayer = webPlayerRef.current;
    if (req.entity_id === WEB_PLAYER_ENTITY && webPlayer) {
      switch (req.command) {
        case 'play': return webPlayer.play();
        case 'pause': return webPlayer.pause();
        case 'next': return webPlayer.next();
        case 'previous': return webPlayer.previous();
        case 'seek': return webPlayer.seek(req.position ?? 0);
        case 'volume_set': return webPlayer.setVolume(Math.round((req.volume_level ?? 0) * 100));
      }
    }
    return api.mediaTransport(req);
  }, []);

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
          const appName = (active as { app_name?: string }).app_name?.trim();
          setMedia({
            entity_id: active.entity_id || '',
            device_name: active.friendly_name || active.entity_id || '',
            title: active.media_title || (appName ? `Playing on ${appName}` : 'Playing something'),
            artist: active.media_artist || '',
            album: active.media_album || '',
            state: active.state || 'idle',
          });
          if (active.duration && active.duration > 0) {
            setDuration(active.duration);
          }
          // A new track restarts at 0, so a zero position is a real reading.
          if (typeof active.position === 'number' && active.position >= 0) {
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
    transport({ entity_id: media.entity_id, command: 'seek', position: Math.round(target) })
      .catch(() => toast.error('Could not seek on that player'));
  }, [media, transport]);

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
      transport({ entity_id: entityId, command: 'volume_set', volume_level: level / 100 })
        .catch(() => toast.error('Could not set the volume on that player'));
    }, CONTROL_DEBOUNCE_MS);
  }, [transport]);

  // Never leave a pending control request firing into an unmounted widget.
  useEffect(() => () => {
    if (seekTimerRef.current) clearTimeout(seekTimerRef.current);
    if (volumeTimerRef.current) clearTimeout(volumeTimerRef.current);
  }, []);

  const playPause = async () => {
    if (!media?.entity_id) return;
    try {
      await transport({
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

  const skip = async (command: 'previous' | 'next') => {
    if (!media?.entity_id) return;
    try {
      await transport({ entity_id: media.entity_id, command });
    } catch {
      toast.error(command === 'next' ? 'Could not skip ahead' : 'Could not go back');
    }
  };

  const progressPercent = duration > 0 ? (position / duration) * 100 : 0;
  const controlButton =
    'flex items-center justify-center w-11 h-11 rounded-full text-slate-400 hover:text-white hover:bg-white/5 transition-colors shrink-0';

  // Laid out for the 200px "wide" cell it defaults to: track info, then one
  // row of transport + volume, then the scrubber. Anything taller scrolls
  // inside the card instead of spilling into the widget below.
  return (
    <div className="glass-card h-full p-4 relative flex flex-col overflow-hidden">
      <div className="absolute top-2 right-2 z-10">{settingsButton}</div>

      {isLoading ? (
        <div className="flex flex-1 items-center justify-center">
          <p className="text-sm text-slate-500 animate-pulse">Loading media...</p>
        </div>
      ) : media ? (
        <div className="flex-1 min-h-0 overflow-y-auto space-y-2">
          <div className="flex items-center gap-3 pr-10">
            <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-purple-500/20 to-pink-500/20 border border-purple-500/30 flex items-center justify-center shrink-0">
              <Music size={20} className="text-purple-400" />
            </div>
            <div className="min-w-0">
              <p className="font-bold text-white truncate">{media.title || 'Playing something'}</p>
              {media.artist && (
                <p className="text-sm text-slate-400 truncate">{media.artist}</p>
              )}
              <p className="text-xs text-slate-500 truncate">{media.device_name}</p>
            </div>
          </div>

          <div className="flex items-center gap-1">
            <button onClick={() => void skip('previous')} className={controlButton} aria-label="Previous track">
              <SkipBack size={18} />
            </button>
            <button
              onClick={playPause}
              aria-label={media.state === 'playing' ? 'Pause' : 'Play'}
              className="w-11 h-11 rounded-full bg-purple-500/30 border border-purple-500/40 flex items-center justify-center text-purple-400 hover:bg-purple-500/40 transition-colors shrink-0"
            >
              {media.state === 'playing' ? <Pause size={18} /> : <Play size={18} />}
            </button>
            <button onClick={() => void skip('next')} className={controlButton} aria-label="Next track">
              <SkipForward size={18} />
            </button>
            <Volume2 size={16} className="text-slate-500 ml-2 shrink-0" aria-hidden />
            <input
              type="range"
              min={0}
              max={100}
              value={volumeLevel}
              aria-label="Volume"
              className="flex-1 min-w-0 h-11 bg-transparent cursor-pointer accent-purple-500"
              onChange={(e) => {
                const next = Number(e.target.value);
                setVolumeLevel(next);
                // Local value moves instantly; the device gets one settled call.
                if (media?.entity_id) commitVolume(media.entity_id, next);
              }}
            />
          </div>

          {duration > 0 && (
            <div className="flex items-center gap-2">
              <div
                onPointerDown={handlePointerDown}
                onKeyDown={handleScrubberKeyDown}
                role="slider"
                tabIndex={0}
                aria-label="Track progress scrubber"
                aria-valuemin={0}
                aria-valuemax={Math.round(duration)}
                aria-valuenow={Math.round(position)}
                aria-valuetext={`${formatDuration(position)} of ${formatDuration(duration)}`}
                className="relative flex-1 py-3 select-none touch-none cursor-pointer group"
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
              <p className="text-[10px] text-slate-500 font-mono shrink-0 tabular-nums">
                {formatDuration(position)} / {formatDuration(duration)}
              </p>
            </div>
          )}
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center">
          <p className="text-sm text-slate-500">No active media</p>
        </div>
      )}
    </div>
  );
};

export default ActiveMediaWidget;
