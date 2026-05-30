import { useState, useRef, useEffect, useCallback } from 'react';
import {
  Play, Pause, Volume2, Volume1, VolumeX,
  Loader2, SkipBack, SkipForward, BookOpen, Music,
  ChevronDown,
} from 'lucide-react';

interface LocalTrack {
  id: string;
  title: string;
  subtitle: string;
  type: 'audiobook' | 'music';
  coverUrl?: string;
  source: 'abs' | 'ma';
}

export const LocalAudioPlayer = ({
  initialTrack,
}: {
  initialTrack?: LocalTrack;
}) => {
  const [track, setTrack] = useState<LocalTrack | null>(initialTrack || null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isLoaded, setIsLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [volume, setVolume] = useState(70);
  const [isMuted, setIsMuted] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [streamUrl, setStreamUrl] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const progressTimerRef = useRef<number | null>(null);

  const handlePlay = useCallback(async () => {
    if (!track) return;
    setIsPlaying(true);
    setError(null);
    try {
      let url = '';
      if (track.source === 'abs') {
        url = `/api/media/stream/audiobookshelf/${track.id}`;
      } else if (track.source === 'ma') {
        url = `/api/media/stream/music-assistant?uri=${encodeURIComponent(track.id)}`;
      }
      setStreamUrl(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start playback');
      setIsPlaying(false);
    }
  }, [track]);

  const handlePause = useCallback(() => {
    setIsPlaying(false);
    if (audioRef.current) {
      audioRef.current.pause();
    }
  }, []);

  const handleToggle = useCallback(() => {
    if (isPlaying) {
      handlePause();
    } else {
      handlePlay();
    }
  }, [isPlaying, handlePlay, handlePause]);

  const handleVolume = useCallback((v: number) => {
    setVolume(v);
    if (audioRef.current) {
      audioRef.current.volume = v / 100;
      audioRef.current.muted = false;
      setIsMuted(false);
    }
  }, []);

  const handleMuteToggle = useCallback(() => {
    if (audioRef.current) {
      const newMuted = !isMuted;
      audioRef.current.muted = newMuted;
      setIsMuted(newMuted);
    }
  }, [isMuted]);

  const handleSeek = useCallback((time: number) => {
    if (audioRef.current) {
      audioRef.current.currentTime = time;
      setCurrentTime(time);
    }
  }, []);

  const handleSkipBack = useCallback(() => {
    if (audioRef.current && audioRef.current.currentTime > 5) {
      audioRef.current.currentTime = 0;
    }
  }, []);

  const handleSkipForward = useCallback(() => {
    if (audioRef.current && duration > 0) {
      audioRef.current.currentTime = Math.min(audioRef.current.currentTime + 30, duration);
    }
  }, [duration]);

  // Setup audio element
  useEffect(() => {
    if (streamUrl) {
      if (!audioRef.current) {
        audioRef.current = new Audio();
      }
      audioRef.current.src = streamUrl;
      audioRef.current.volume = volume / 100;
      audioRef.current.muted = isMuted;

      const onLoaded = () => {
        setIsLoaded(true);
        setDuration(audioRef.current?.duration || 0);
        audioRef.current?.play().catch(() => {
          setIsLoaded(false);
          setError('Playback failed. Tap play to start.');
        });
      };

      const onEnded = () => {
        setIsPlaying(false);
        setIsLoaded(false);
      };

      const onError = () => {
        setIsPlaying(false);
        setIsLoaded(false);
        setError('Failed to load stream. Check your connection.');
      };

      audioRef.current.addEventListener('loadeddata', onLoaded);
      audioRef.current.addEventListener('ended', onEnded);
      audioRef.current.addEventListener('error', onError);

      return () => {
        audioRef.current?.removeEventListener('loadeddata', onLoaded);
        audioRef.current?.removeEventListener('ended', onEnded);
        audioRef.current?.removeEventListener('error', onError);
      };
    }
  }, [streamUrl, volume, isMuted]);

  // Progress timer
  useEffect(() => {
    if (isPlaying && audioRef.current) {
      progressTimerRef.current = window.setInterval(() => {
        if (audioRef.current && !audioRef.current.paused) {
          setCurrentTime(audioRef.current.currentTime);
        }
      }, 1000);
    }
    return () => {
      if (progressTimerRef.current) {
        clearInterval(progressTimerRef.current);
      }
    };
  }, [isPlaying]);

  // Cleanup
  useEffect(() => {
    return () => {
      audioRef.current?.pause();
      audioRef.current?.src = '';
    };
  }, []);

  if (!track) return null;

  const formatTime = (seconds: number) => {
    if (!seconds || isNaN(seconds)) return '0:00';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  const formatHours = (seconds: number) => {
    if (!seconds || isNaN(seconds)) return '0m';
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    if (hrs > 0) return `${hrs}h ${mins}m`;
    return `${mins}m`;
  };

  const progressPercent = duration > 0 ? (currentTime / duration) * 100 : 0;

  return (
    <div className="relative">
      {/* Compact mode - shown when collapsed */}
      {!isExpanded && (
        <button
          onClick={() => setIsExpanded(true)}
          className="w-full glass-panel rounded-xl p-3 flex items-center gap-3 text-left transition-all hover:bg-white/10 border border-cyan-500/20"
        >
          <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-cyan-500/20 to-purple-500/20 flex items-center justify-center shrink-0">
            {track.type === 'audiobook' ? <BookOpen size={18} className="text-amber-400" /> : <Music size={18} className="text-purple-400" />}
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-white text-sm font-medium truncate">{track.title}</p>
            <p className="text-xs text-slate-400 truncate">{track.subtitle}</p>
          </div>
          <div className="flex items-center gap-2">
            {isPlaying ? (
              <div className="flex items-center gap-1">
                <div className="w-0.5 h-3 bg-cyan-400 rounded-full animate-pulse" />
                <div className="w-0.5 h-4 bg-cyan-400 rounded-full animate-pulse" style={{ animationDelay: '0.1s' }} />
                <div className="w-0.5 h-2 bg-cyan-400 rounded-full animate-pulse" style={{ animationDelay: '0.2s' }} />
              </div>
            ) : (
              <Play size={16} className="text-cyan-400" />
            )}
          </div>
        </button>
      )}

      {/* Expanded mode - full player */}
      {isExpanded && (
        <div className="fixed inset-0 bg-black/90 backdrop-blur-md z-50 flex items-center justify-center p-4">
          <div className="w-full max-w-lg glass-panel rounded-2xl p-6 border border-white/10 shadow-2xl">
            {/* Header */}
            <div className="flex items-center justify-between mb-6">
              <div className="flex items-center gap-2">
                {track.type === 'audiobook' ? <BookOpen size={20} className="text-amber-400" /> : <Music size={20} className="text-purple-400" />}
                <span className="text-xs text-slate-500 uppercase tracking-wider">
                  {track.type === 'audiobook' ? 'Audiobook' : 'Playing'}
                </span>
              </div>
              <button
                onClick={() => setIsExpanded(false)}
                className="text-slate-400 hover:text-white p-2 rounded-lg hover:bg-white/10 transition-colors"
              >
                <ChevronDown size={20} />
              </button>
            </div>

            {/* Track info */}
            <div className="text-center mb-6">
              <p className="text-white font-semibold text-xl mb-1">{track.title}</p>
              <p className="text-slate-400 text-sm">{track.subtitle}</p>
            </div>

            {/* Progress bar */}
            <div className="mb-6">
              <div
                className="w-full h-2 bg-white/10 rounded-full cursor-pointer relative group"
                onClick={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  const x = e.clientX - rect.left;
                  const percent = x / rect.width;
                  handleSeek(percent * duration);
                }}
              >
                <div
                  className="h-full bg-gradient-to-r from-cyan-400 to-purple-400 rounded-full transition-all relative"
                  style={{ width: `${progressPercent}%` }}
                >
                  <div className="absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full shadow-lg opacity-0 group-hover:opacity-100 transition-opacity" />
                </div>
              </div>
              <div className="flex justify-between mt-2 text-xs text-slate-500">
                <span>{formatTime(currentTime)}</span>
                <span>{formatHours(duration)}</span>
              </div>
            </div>

            {/* Controls */}
            <div className="flex items-center justify-center gap-6 mb-6">
              <button
                onClick={handleSkipBack}
                className="text-slate-400 hover:text-white transition-colors p-2 rounded-lg hover:bg-white/5"
              >
                <SkipBack size={20} />
              </button>
              <button
                onClick={handleToggle}
                disabled={!isLoaded && !isPlaying}
                className="w-16 h-16 rounded-full bg-gradient-to-br from-cyan-400 to-purple-400 flex items-center justify-center text-white hover:scale-105 transition-all disabled:opacity-50"
              >
                {isLoaded || isPlaying ? (
                  isPlaying ? <Pause size={24} /> : <Play size={24} className="ml-1" />
                ) : (
                  <Loader2 size={24} className="animate-spin" />
                )}
              </button>
              <button
                onClick={handleSkipForward}
                className="text-slate-400 hover:text-white transition-colors p-2 rounded-lg hover:bg-white/5"
              >
                <SkipForward size={20} />
              </button>
            </div>

            {/* Volume control */}
            <div className="flex items-center gap-3 mb-6">
              <button
                onClick={handleMuteToggle}
                className="text-slate-400 hover:text-white transition-colors p-1.5 rounded-lg hover:bg-white/5"
              >
                {isMuted || volume === 0 ? (
                  <VolumeX size={16} />
                ) : volume < 50 ? (
                  <Volume1 size={16} />
                ) : (
                  <Volume2 size={16} />
                )}
              </button>
              <input
                type="range"
                min="0"
                max="100"
                value={isMuted ? 0 : volume}
                onChange={(e) => handleVolume(Number(e.target.value))}
                className="flex-1 accent-cyan-400"
              />
              <span className="text-xs text-slate-500 w-8 text-right tabular-nums">{isMuted ? 'M' : volume}</span>
            </div>

            {/* Error message */}
            {error && (
              <div className="mt-4 p-3 rounded-xl bg-red-500/10 border border-red-500/20 text-center">
                <p className="text-sm text-red-400">{error}</p>
              </div>
            )}

            {/* Close button */}
            <button
              onClick={() => {
                setIsExpanded(false);
                setTrack(null);
                setIsPlaying(false);
                setIsLoaded(false);
                setStreamUrl(null);
              }}
              className="w-full mt-4 py-2 text-sm text-slate-500 hover:text-red-400 transition-colors"
            >
              Stop Playback
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
