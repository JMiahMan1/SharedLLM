import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  BatteryMedium, CameraOff, Cctv, ChevronDown, ChevronLeft, ChevronRight, Film, Loader2, Moon, Pause, Play, RefreshCw,
  RotateCw, X,
} from 'lucide-react';
import { api } from '../services/api';
import { useMediaToken } from '../lib/mediaToken';
import { useHaptics } from '../hooks/useHaptics';
import { errorText } from '../lib/errorText';
import PageHeader from '../components/ui/PageHeader';
import { CardSkeletons, EmptyState, ErrorRetry } from '../components/ui/StateViews';
import { useAuth } from '../context/AuthContext';
import { browserCaps, playbackModes } from '../lib/cameraLive';
import RealtimePlayer from '../components/cameras/RealtimePlayer';
import LiveVideoSettings from '../components/cameras/LiveVideoSettings';
import MotionRecordingSettings from '../components/cameras/MotionRecordingSettings';
import WhoCanWatch from '../components/cameras/WhoCanWatch';
import CameraControls from '../components/cameras/CameraControls';
import { updateEntity } from '../lib/hardware';
import {
  addDays, cameraMediaUrl, cameraRotation, dayRange, formatClock, formatDuration, fromDateInput, isCameraOnline,
  nextRotation, playbackRotation, rotatedMediaStyle, sameDay, tileRefreshMs, toDateInput, triggerLabel, triggerStyle,
  type CameraInfo, type CameraRotation,
} from '../lib/cameras';
import {
  DAY_MS, KIND_COLORS, buildSegments, dayBounds, filterSegments, followingSegment, initialPlayhead, kindsPresent,
  nextSegment, previousSegment, resolvePlayTarget, segmentAt, type Segment, type TimelineKind,
} from '../lib/cameraTimeline';
import RecordingsTimeline, { TimelineControls, TimelineFilters } from '../components/cameras/RecordingsTimeline';

const LIST_POLL_MS = 30_000;
/** A battery camera's live view pauses after this long, since every frame keeps it awake. */
export const BATTERY_LIVE_LIMIT_MS = 2 * 60_000;

const TOUCH = 'min-h-11 min-w-11';

/** "Garage Camera 1 Fluent" reads better without the stream's name. */
const displayName = (camera: CameraInfo) =>
  (camera.name || camera.entity_id).replace(/\s+(fluent|clear|sub|main)$/i, '');

/** Re-render on a timer while the page is visible (a tile's picture refresh). */
function useTick(ms: number | null): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!ms) return undefined;
    const id = window.setInterval(() => {
      if (!document.hidden) setTick((t) => t + 1);
    }, ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return tick;
}

function CameraBadges({ camera }: { camera: CameraInfo }) {
  return (
    <span className="flex items-center gap-1.5 shrink-0">
      {camera.sleeping && (
        <span className="flex items-center gap-1 rounded-full bg-indigo-500/15 border border-indigo-500/30 px-2 py-0.5 text-[11px] text-indigo-200">
          <Moon size={11} aria-hidden />Asleep
        </span>
      )}
      {camera.battery && <BatteryMedium size={14} className="text-slate-400" aria-label="Battery camera" />}
      {camera.recordings && <Film size={14} className="text-slate-400" aria-label="Has recordings" />}
    </span>
  );
}

function CameraTile({ camera, token, onOpen }: {
  camera: CameraInfo;
  token: ReturnType<typeof useMediaToken>;
  onOpen: (camera: CameraInfo) => void;
}) {
  const online = isCameraOnline(camera);
  const tick = useTick(online ? tileRefreshMs(camera) : null);
  const [retry, setRetry] = useState(0);
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const src = online ? cameraMediaUrl(token, camera.entity_id, 'snapshot', { t: `${tick}.${retry}` }) : null;

  useEffect(() => {
    // A new picture is on its way; a failure belongs to the old one.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset per picture
    setFailed(false);
  }, [src]);

  const name = displayName(camera);
  return (
    <div className="glass-panel rounded-2xl overflow-hidden flex flex-col">
      <button
        type="button"
        onClick={() => onOpen(camera)}
        className="relative block w-full aspect-video overflow-hidden bg-slate-900/80 text-left"
        aria-label={`Open ${name}`}
      >
        {src && !failed && (
          <img
            src={src}
            alt=""
            className={`absolute inset-0 h-full w-full object-cover transition-opacity ${loaded ? 'opacity-100' : 'opacity-0'}`}
            style={rotatedMediaStyle(cameraRotation(camera))}
            onLoad={() => setLoaded(true)}
            onError={() => setFailed(true)}
          />
        )}
        {(!online || failed) && (
          <span className="absolute inset-0 flex flex-col items-center justify-center gap-1 text-slate-400">
            <CameraOff size={22} aria-hidden />
            <span className="text-xs">{online ? 'Not answering' : 'Offline'}</span>
          </span>
        )}
        {online && !failed && !loaded && (
          <span className="absolute inset-0 flex flex-col items-center justify-center gap-1 text-slate-500" role="status" aria-label={`Loading ${name}`}>
            <Loader2 className="animate-spin" size={20} aria-hidden />
            {/* A sleeping battery camera takes about 15 s to wake for its first picture. */}
            {camera.battery && <span className="text-[11px]">Waking the camera…</span>}
          </span>
        )}
      </button>
      <div className="flex items-center gap-2 px-3 py-2 min-h-11">
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-white truncate">{name}</span>
          {camera.area && <span className="block text-[11px] text-slate-400 truncate">{camera.area}</span>}
        </span>
        <CameraBadges camera={camera} />
        {online && (
          <button
            type="button"
            onClick={() => { setLoaded(false); setRetry((r) => r + 1); }}
            className={`${TOUCH} -mr-2 flex items-center justify-center rounded-xl text-slate-400 hover:bg-white/10 hover:text-white`}
            aria-label={`Refresh ${name}`}
          >
            <RefreshCw size={15} />
          </button>
        )}
      </div>
    </div>
  );
}

function LiveView({ camera, token }: { camera: CameraInfo; token: ReturnType<typeof useMediaToken> }) {
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [paused, setPaused] = useState(false);
  // go2rtc's real-time video when the hub offers it and this browser can play it; the picture stream otherwise.
  const [realtimeFailed, setRealtimeFailed] = useState(false);
  const online = isCameraOnline(camera);
  const realtime = online && !paused && !realtimeFailed && playbackModes(camera.realtime, browserCaps()).length > 0;

  // Every frame keeps a battery camera awake, so its live view stops on its own.
  useEffect(() => {
    if (!camera.battery || paused) return undefined;
    const id = window.setTimeout(() => setPaused(true), BATTERY_LIVE_LIMIT_MS);
    return () => window.clearTimeout(id);
  }, [camera.battery, paused, attempt]);

  const src = online && !paused && !realtime
    ? cameraMediaUrl(token, camera.entity_id, 'stream', { n: String(attempt) }) : null;
  const again = () => {
    setFailed(false); setLoaded(false); setPaused(false); setRealtimeFailed(false); setAttempt((a) => a + 1);
  };

  return (
    <div className="space-y-2">
      {realtime && (
        <RealtimePlayer key={attempt} camera={camera} token={token} onGiveUp={() => setRealtimeFailed(true)} />
      )}
      <div className={`relative w-full aspect-video rounded-xl overflow-hidden bg-black ${realtime ? 'hidden' : ''}`}>
        {src && !failed && (
          <img
            key={src}
            src={src}
            alt={`${displayName(camera)} live`}
            className="absolute inset-0 h-full w-full object-contain"
            style={rotatedMediaStyle(cameraRotation(camera))}
            onLoad={() => setLoaded(true)}
            onError={() => setFailed(true)}
          />
        )}
        {src && !failed && !loaded && (
          <span className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-slate-400" role="status">
            <Loader2 className="animate-spin" size={22} aria-hidden />
            <span className="text-xs">{camera.battery ? 'Waking the camera…' : 'Connecting…'}</span>
          </span>
        )}
        {(!online || failed) && (
          <span className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-4 text-center">
            <CameraOff size={26} className="text-slate-400" aria-hidden />
            <span className="text-sm text-slate-300">
              {online ? `${displayName(camera)} is not answering` : `${displayName(camera)} is offline`}
            </span>
            {camera.sleeping && <span className="text-xs text-slate-500">It is asleep; it may take a few seconds to wake.</span>}
            <button type="button" onClick={again} className={`${TOUCH} px-4 text-sm text-cyan-300 underline`}>Try again</button>
          </span>
        )}
        {paused && online && (
          <span className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-4 text-center bg-black/70">
            <span className="text-sm text-slate-200">Paused to save the camera’s battery</span>
            <button
              type="button"
              onClick={again}
              className={`${TOUCH} flex items-center gap-2 rounded-xl bg-cyan-500/20 border border-cyan-500/40 px-4 text-sm text-cyan-100`}
            >
              <Play size={16} aria-hidden />Keep watching
            </button>
          </span>
        )}
        {src && loaded && !failed && (
          <span className="absolute left-2 top-2 flex items-center gap-1.5 rounded-full bg-black/60 px-2 py-0.5 text-[11px] font-semibold text-red-300">
            <span className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" aria-hidden />LIVE
          </span>
        )}
      </div>
      <p className="text-xs text-slate-500">
        {realtime
          ? 'Real-time video.'
          : camera.stream === 'camera'
          ? 'The camera’s own video.'
          : `A fresh picture about every ${Math.max(0.5, camera.snapshot_interval ?? 1)} s.`}
        {camera.battery ? ' Battery camera: live view stops after two minutes.' : ''}
      </p>
    </div>
  );
}

/** Where to put a playing recording: `at` seconds in; a new `n` asks again for the same second. */
export interface RecordingSeek {
  at: number;
  n: number;
}

/**
 * A recording. Unturned, it is the browser's own player. Turned, the video is
 * rotated without its controls (a sideways scrub bar is no use), and play,
 * pause and seeking sit under it the right way up. `seek` starts it part way
 * in (a tap on the timeline lands inside a clip); `onTime` and `onEnded` let
 * the timeline follow it and carry on into the next clip.
 */
function RecordingPlayer({ src, rotation, label, onError, seek, onTime, onEnded }: {
  src: string;
  rotation: CameraRotation;
  label: string;
  onError: () => void;
  seek?: RecordingSeek | null;
  onTime?: (seconds: number) => void;
  onEnded?: () => void;
}) {
  const ref = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  // A seek asked for before the file's length is known waits for it.
  const pendingSeek = useRef<number | null>(null);
  useEffect(() => {
    if (!seek) return;
    const v = ref.current;
    if (v && v.readyState >= 1) {
      v.currentTime = seek.at;
      if (v.paused) void v.play()?.catch?.(() => {});
    } else {
      pendingSeek.current = seek.at;
    }
  }, [seek]);
  const onMetadata = (v: HTMLVideoElement) => {
    setDuration(Number.isFinite(v.duration) ? v.duration : 0);
    if (pendingSeek.current !== null) {
      v.currentTime = pendingSeek.current;
      pendingSeek.current = null;
    }
  };
  const onTimeUpdate = (v: HTMLVideoElement) => {
    setTime(v.currentTime);
    onTime?.(v.currentTime);
  };
  if (rotation === 0) {
    return (
      <video ref={ref} src={src} controls autoPlay playsInline preload="metadata"
        className="w-full aspect-video rounded-xl bg-black" onError={onError} aria-label={label}
        onLoadedMetadata={(e) => onMetadata(e.currentTarget)}
        onTimeUpdate={(e) => onTimeUpdate(e.currentTarget)}
        onEnded={onEnded} />
    );
  }
  const toggle = () => {
    const v = ref.current;
    if (!v) return;
    if (v.paused) void v.play()?.catch?.(() => {}); else v.pause();
  };
  return (
    <div className="space-y-1">
      <div className="relative w-full aspect-video rounded-xl overflow-hidden bg-black">
        <video
          ref={ref}
          src={src}
          autoPlay
          playsInline
          preload="metadata"
          className="absolute inset-0 h-full w-full object-contain"
          style={rotatedMediaStyle(rotation)}
          onClick={toggle}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onTimeUpdate={(e) => onTimeUpdate(e.currentTarget)}
          onLoadedMetadata={(e) => onMetadata(e.currentTarget)}
          onDurationChange={(e) => setDuration(Number.isFinite(e.currentTarget.duration) ? e.currentTarget.duration : 0)}
          onEnded={onEnded}
          onError={onError}
          aria-label={label}
        />
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={toggle}
          className={`${TOUCH} flex items-center justify-center rounded-xl bg-white/5 border border-white/10 text-white hover:bg-white/10`}
          aria-label={playing ? 'Pause' : 'Play'}
        >
          {playing ? <Pause size={16} /> : <Play size={16} />}
        </button>
        <input
          type="range"
          min={0}
          max={duration || 0}
          step={0.1}
          value={Math.min(time, duration || 0)}
          disabled={!duration}
          onChange={(e) => { const v = ref.current; if (v) v.currentTime = Number(e.target.value); }}
          className="min-h-11 flex-1 min-w-0 accent-cyan-400"
          aria-label="Position"
        />
        <span className="text-xs text-slate-400 tabular-nums shrink-0">{formatDuration(time)} / {formatDuration(duration)}</span>
      </div>
    </div>
  );
}

/**
 * A day's recordings, laid out like a recorder's playback screen: the player on
 * top, and under it the day as a horizontal track of coloured clips beneath a
 * fixed centre playhead. Tap a clip (or a moment in one) to play from there;
 * when a clip ends the next one plays and the playhead follows the video. Type
 * chips filter the track, and the full list is one tap away but folded, so a
 * busy day never pushes the player off the screen.
 */
function RecordingsView({ camera, token }: { camera: CameraInfo; token: ReturnType<typeof useMediaToken> }) {
  const [day, setDay] = useState(() => new Date());
  const [playingFile, setPlayingFile] = useState<string | null>(null);
  const [seek, setSeek] = useState<RecordingSeek | null>(null);
  const [playError, setPlayError] = useState(false);
  // Null until the day is moved through: the day then opens on its latest recording.
  const [playhead, setPlayhead] = useState<number | null>(null);
  const [spanMs, setSpanMs] = useState(DAY_MS);
  const [filter, setFilter] = useState<TimelineKind | 'all'>('all');
  const [listOpen, setListOpen] = useState(false);
  const scrubbing = useRef(false);
  useTick(60_000); // the "now" marker on today
  const today = new Date();
  const isToday = sameDay(day, today);
  const range = useMemo(() => dayRange(day), [day]);
  const bounds = useMemo(() => dayBounds(day), [day]);

  const recordings = useQuery({
    queryKey: ['camera-recordings', camera.entity_id, toDateInput(day)],
    queryFn: () => api.getCameraRecordings(camera.entity_id, range.start, range.end),
    // Today's list grows while you watch; past days do not change.
    refetchInterval: () => (isToday && !document.hidden ? 60_000 : false),
  });
  const items = useMemo(() => recordings.data?.recordings ?? [], [recordings.data]);
  const segments = useMemo(() => buildSegments(items), [items]);
  const kinds = useMemo(() => kindsPresent(segments), [segments]);
  // A filter for a type the day no longer has (another day) shows everything.
  const activeFilter = filter !== 'all' && kinds.some((k) => k.kind === filter) ? filter : 'all';
  const shown = useMemo(() => filterSegments(segments, activeFilter), [segments, activeFilter]);
  const nowMs = isToday ? today.getTime() : null;
  const head = playhead ?? initialPlayhead(segments, bounds.start, bounds.end, today.getTime());
  const playing = segments.find((s) => s.rec.file === playingFile) ?? null;

  const changeDay = (next: Date) => {
    setDay(next);
    setPlayhead(null);
    setPlayingFile(null);
    setSeek(null);
    setPlayError(false);
  };

  const play = (seg: Segment, offsetS: number) => {
    setPlayError(false);
    setPlayingFile(seg.rec.file);
    setSeek((s) => ({ at: offsetS, n: (s?.n ?? 0) + 1 }));
    setPlayhead(seg.startMs + offsetS * 1000);
  };
  const commit = (t: number, toleranceMs: number) => {
    scrubbing.current = false;
    const target = resolvePlayTarget(shown, t, toleranceMs);
    if (target) play(target.seg, target.offsetS);
    else setPlayhead(t);
  };
  const scrub = (t: number) => { scrubbing.current = true; setPlayhead(t); };
  const onTime = (seconds: number) => {
    if (!playing || scrubbing.current) return;
    setPlayhead(playing.startMs + seconds * 1000);
  };
  const onEnded = () => {
    if (!playing) return;
    const next = followingSegment(shown, playing);
    if (next) play(next, 0);
  };
  const previous = previousSegment(shown, head);
  const next = nextSegment(shown, head);
  const playUrl = playing ? cameraMediaUrl(token, camera.entity_id, 'play', { file: playing.rec.file }) : null;
  const here = segmentAt(shown, head);

  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => changeDay(addDays(day, -1))}
          className={`${TOUCH} flex items-center justify-center rounded-xl bg-white/5 border border-white/10 text-white hover:bg-white/10`}
          aria-label="Previous day"
        >
          <ChevronLeft size={18} />
        </button>
        <input
          type="date"
          value={toDateInput(day)}
          max={toDateInput(today)}
          onChange={(e) => { const d = fromDateInput(e.target.value); if (d && d <= today) changeDay(d); }}
          className="min-h-11 flex-1 min-w-0 rounded-xl bg-white/5 border border-white/10 px-3 text-sm text-white [color-scheme:dark]"
          aria-label="Day"
        />
        <button
          type="button"
          onClick={() => changeDay(addDays(day, 1))}
          disabled={isToday}
          className={`${TOUCH} flex items-center justify-center rounded-xl bg-white/5 border border-white/10 text-white hover:bg-white/10 disabled:opacity-40`}
          aria-label="Next day"
        >
          <ChevronRight size={18} />
        </button>
      </div>

      {playing && playUrl && !playError ? (
        <RecordingPlayer
          key={playing.rec.file}
          src={playUrl}
          rotation={playbackRotation(camera, playing.rec)}
          label={`Recording at ${formatClock(playing.rec.start)}`}
          onError={() => setPlayError(true)}
          seek={seek}
          onTime={onTime}
          onEnded={onEnded}
        />
      ) : (
        <div className="w-full aspect-video rounded-xl bg-black flex flex-col items-center justify-center gap-2 text-center p-4"
          data-testid="recording-placeholder">
          {playError ? (
            <>
              <span className="text-sm text-slate-300">This recording would not play.</span>
              <button type="button" onClick={() => setPlayError(false)} className={`${TOUCH} px-4 text-sm text-cyan-300 underline`}>
                Try again
              </button>
            </>
          ) : playing ? (
            <span className="text-sm text-slate-300">Getting ready…</span>
          ) : segments.length > 0 ? (
            <>
              <Film size={22} className="text-slate-500" aria-hidden />
              <span className="text-sm text-slate-300">
                {here ? 'Tap the timeline or Play to watch.' : 'No recording at this time. Tap a coloured clip to watch it.'}
              </span>
              {(here ?? next ?? previous) && (
                <button
                  type="button"
                  onClick={() => { const seg = here ?? next ?? previous; if (seg) play(seg, here ? Math.max(0, (head - seg.startMs) / 1000) : 0); }}
                  className={`${TOUCH} flex items-center gap-2 rounded-xl bg-cyan-500/20 border border-cyan-500/40 px-4 text-sm text-cyan-100`}
                >
                  <Play size={16} aria-hidden />Play
                </button>
              )}
            </>
          ) : null}
        </div>
      )}

      {recordings.isLoading ? (
        <div className="flex justify-center py-6" role="status" aria-label="Loading recordings">
          <Loader2 className="animate-spin text-slate-500" />
        </div>
      ) : recordings.isError ? (
        <div className="text-center py-4">
          <p className="text-sm text-red-300">{errorText(recordings.error, 'Couldn’t read the recordings')}</p>
          <button type="button" onClick={() => void recordings.refetch()} className={`${TOUCH} px-4 text-sm text-cyan-300 underline`}>
            Try again
          </button>
        </div>
      ) : segments.length === 0 ? (
        <p className="text-sm text-slate-500 py-4 text-center">No recordings {isToday ? 'yet today' : 'on this day'}.</p>
      ) : (
        <>
          <RecordingsTimeline
            segments={shown}
            dayStart={bounds.start}
            dayEnd={bounds.end}
            now={nowMs}
            playhead={head}
            spanMs={spanMs}
            playingFile={playingFile}
            onSpan={setSpanMs}
            onScrub={scrub}
            onCommit={commit}
          />
          <TimelineControls
            spanMs={spanMs}
            onSpan={setSpanMs}
            hasPrevious={Boolean(previous)}
            hasNext={Boolean(next)}
            onPrevious={() => { if (previous) play(previous, 0); }}
            onNext={() => { if (next) play(next, 0); }}
          />
          <TimelineFilters kinds={kinds} filter={activeFilter} onFilter={setFilter} total={segments.length} />
          <details className="rounded-xl border border-white/10 bg-white/[0.03] group" onToggle={(e) => setListOpen(e.currentTarget.open)}>
            <summary className="min-h-11 flex cursor-pointer list-none items-center gap-2 px-3 text-sm text-slate-300">
              <ChevronDown size={16} className="transition-transform group-open:rotate-180" aria-hidden />
              List ({shown.length})
            </summary>
            {listOpen && (
              <ul className="max-h-72 overflow-y-auto space-y-1 px-2 pb-2" aria-label="Recordings">
                {[...shown].reverse().map((seg) => {
                  const selected = playingFile === seg.rec.file;
                  return (
                    <li key={seg.rec.file}>
                      <button
                        type="button"
                        onClick={() => play(seg, 0)}
                        aria-pressed={selected}
                        className={`w-full min-h-11 flex items-center gap-2 rounded-lg border px-2.5 text-left transition-colors ${
                          selected ? 'bg-cyan-500/15 border-cyan-500/40' : 'bg-white/5 border-white/10 hover:bg-white/10'
                        }`}
                      >
                        <span className={`h-2.5 w-2.5 shrink-0 rounded-sm ${KIND_COLORS[seg.kind]}`} aria-hidden />
                        <span className="text-sm text-white tabular-nums shrink-0">{formatClock(seg.rec.start)}</span>
                        <span className="text-xs text-slate-400 tabular-nums shrink-0">{formatDuration(seg.rec.duration)}</span>
                        <span className="flex flex-wrap gap-1 min-w-0 justify-end ml-auto">
                          {seg.rec.triggers.map((t) => (
                            <span key={t} className={`rounded-full border px-2 py-0.5 text-[11px] ${triggerStyle(t)}`}>{triggerLabel(t)}</span>
                          ))}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </details>
        </>
      )}
    </div>
  );
}

function CameraViewer({ camera, token, view, onView, onClose }: {
  camera: CameraInfo;
  token: ReturnType<typeof useMediaToken>;
  view: 'live' | 'recordings';
  onView: (view: 'live' | 'recordings') => void;
  onClose: () => void;
}) {
  const isAdmin = Boolean(useAuth().user?.is_admin);
  const queryClient = useQueryClient();
  const rotation = cameraRotation(camera);
  const [whoOpen, setWhoOpen] = useState(false);
  const rotate = useMutation({
    mutationFn: (next: CameraRotation) => updateEntity(camera.entity_id, { rotation: next }),
    onSuccess: (_out, next) => {
      queryClient.setQueryData<CameraInfo[]>(['cameras'], (list) =>
        list?.map((c) => (c.entity_id === camera.entity_id ? { ...c, rotation: next } : c)));
      void queryClient.invalidateQueries({ queryKey: ['cameras'] });
    },
  });
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; }, [onClose]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') closeRef.current(); };
    window.addEventListener('keydown', onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = overflow;
    };
  }, []);

  const tab = camera.recordings ? view : 'live';
  const tabClass = (active: boolean) =>
    `min-h-11 flex-1 rounded-xl px-4 text-sm font-medium transition-colors ${
      active ? 'bg-cyan-500/20 text-cyan-100 border border-cyan-500/40' : 'text-slate-300 hover:bg-white/5 border border-transparent'
    }`;

  return (
    <div
      className="fixed inset-0 z-50 bg-slate-950/95 backdrop-blur-sm overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-label={displayName(camera)}
    >
      <div className="mx-auto w-full max-w-6xl px-3 sm:px-6 pb-10 pt-[max(0.75rem,env(safe-area-inset-top))] space-y-3">
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold text-white truncate">{displayName(camera)}</h2>
            <p className="text-xs text-slate-400 truncate">{[camera.area, camera.model].filter(Boolean).join(' · ')}</p>
          </div>
          <CameraBadges camera={camera} />
          {isAdmin && (
            <button
              type="button"
              onClick={() => rotate.mutate(nextRotation(rotation))}
              disabled={rotate.isPending}
              className={`${TOUCH} flex items-center justify-center rounded-xl bg-white/5 border border-white/10 text-white hover:bg-white/10 disabled:opacity-50`}
              aria-label={`Rotate ${displayName(camera)} (now ${rotation}°)`}
              title="Rotate the picture a quarter turn, for everyone"
              data-testid="camera-rotate"
            >
              {rotate.isPending ? <Loader2 size={18} className="animate-spin" /> : <RotateCw size={18} />}
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            className={`${TOUCH} flex items-center justify-center rounded-xl bg-white/5 border border-white/10 text-white hover:bg-white/10`}
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>
        {rotate.isError && (
          <p className="text-xs text-red-300" role="alert">{errorText(rotate.error, 'Couldn’t rotate the picture')}</p>
        )}
        {camera.recordings && (
          <div className="flex gap-2 rounded-2xl bg-white/5 p-1" role="tablist">
            <button type="button" role="tab" aria-selected={tab === 'live'} className={tabClass(tab === 'live')} onClick={() => onView('live')}>
              Live
            </button>
            <button type="button" role="tab" aria-selected={tab === 'recordings'} className={tabClass(tab === 'recordings')} onClick={() => onView('recordings')}>
              Recordings
            </button>
          </div>
        )}
        {/* Phones stack the picture and the controls; wide screens put the controls beside the picture. */}
        <div className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)] lg:items-start">
          <div className="min-w-0">
            {tab === 'live' ? <LiveView camera={camera} token={token} /> : <RecordingsView camera={camera} token={token} />}
          </div>
          <div className="min-w-0 space-y-3">
            <CameraControls entityId={camera.entity_id} cameraName={displayName(camera)} isAdmin={isAdmin} />
            {isAdmin && (
              <details className="glass-panel rounded-2xl p-4 group" onToggle={(e) => setWhoOpen(e.currentTarget.open)}>
                <summary className="min-h-11 flex cursor-pointer list-none items-center gap-2 text-sm font-medium text-white">
                  <ChevronDown size={16} className="transition-transform group-open:rotate-180" aria-hidden />
                  Who can watch
                </summary>
                {/* Read only once opened: most visits to a camera are to watch it. */}
                {whoOpen && <div className="pt-3"><WhoCanWatch entityId={camera.entity_id} cameraName={displayName(camera)} /></div>}
              </details>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The household's cameras: a grid of tiles with a recent picture, a full live
 * view on tap, and for cameras that record (Reolink, on the Home Hub) a day's
 * recordings with what set each one off. Everything goes through the gateway,
 * which only lists cameras the signed-in person may see; pictures and video are
 * signed with the media token because an `<img>` or `<video>` cannot send the key.
 *
 * Battery cameras wake for every picture, so their tiles do not refresh on their
 * own and their live view pauses after two minutes.
 */
const Cameras = () => {
  const token = useMediaToken();
  const isAdmin = Boolean(useAuth().user?.is_admin);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [recordingOpen, setRecordingOpen] = useState(false);
  const { trigger } = useHaptics();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const openedHere = useRef(false);

  const cameras = useQuery({
    queryKey: ['cameras'],
    queryFn: () => api.getCameras(),
    refetchInterval: () => (document.hidden ? false : LIST_POLL_MS),
  });
  const list = useMemo(
    () => [...(cameras.data ?? [])].sort((a, b) =>
      Number(isCameraOnline(b)) - Number(isCameraOnline(a))
      || (a.area ?? '').localeCompare(b.area ?? '')
      || displayName(a).localeCompare(displayName(b))),
    [cameras.data],
  );

  const selectedId = params.get('camera');
  const selected = list.find((c) => c.entity_id === selectedId) ?? null;
  const view = params.get('view') === 'recordings' ? 'recordings' : 'live';

  const open = useCallback((camera: CameraInfo) => {
    void trigger('light');
    openedHere.current = true;
    // A history entry, so the phone's back button closes the viewer.
    setParams({ camera: camera.entity_id });
  }, [setParams, trigger]);

  const close = useCallback(() => {
    if (openedHere.current) {
      openedHere.current = false;
      navigate(-1);
    } else {
      setParams({}, { replace: true });
    }
  }, [navigate, setParams]);

  const setView = useCallback((next: 'live' | 'recordings') => {
    if (!selectedId) return;
    setParams(next === 'recordings' ? { camera: selectedId, view: next } : { camera: selectedId }, { replace: true });
  }, [selectedId, setParams]);

  return (
    <div className="mx-auto w-full max-w-6xl space-y-4 pb-24" data-testid="cameras-page">
      <PageHeader icon={Cctv} tone="text-cyan-400" title="Cameras"
        subtitle="Tap a camera to watch it live or see today's recordings." />

      {cameras.isLoading ? (
        <CardSkeletons count={3} height="aspect-video h-auto" label="Loading cameras" />
      ) : cameras.isError ? (
        <ErrorRetry title={errorText(cameras.error, 'Could not reach the cameras')} onRetry={() => void cameras.refetch()} />
      ) : list.length === 0 ? (
        <EmptyState
          icon={<Cctv size={28} />}
          title="No cameras yet"
          detail={isAdmin ? 'Add one in Devices; it shows up here as soon as it is set up.' : 'Cameras you are allowed to see show up here.'}
          action={isAdmin ? { label: 'Go to Devices', to: '/devices' } : null}
        />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {list.map((camera) => (
            <CameraTile key={camera.entity_id} camera={camera} token={token} onOpen={open} />
          ))}
        </div>
      )}

      {isAdmin && (
        <details className="glass-panel rounded-2xl p-4 group" onToggle={(e) => setSettingsOpen(e.currentTarget.open)}>
          <summary className="min-h-11 flex cursor-pointer list-none items-center gap-2 text-sm font-medium text-white">
            <ChevronDown size={16} className="transition-transform group-open:rotate-180" aria-hidden />
            Live video settings
          </summary>
          {/* Read from the hub only once opened: most visits to this page are to watch, not to configure. */}
          {settingsOpen && <div className="pt-3"><LiveVideoSettings /></div>}
        </details>
      )}

      {isAdmin && (
        <details className="glass-panel rounded-2xl p-4 group" onToggle={(e) => setRecordingOpen(e.currentTarget.open)}>
          <summary className="min-h-11 flex cursor-pointer list-none items-center gap-2 text-sm font-medium text-white">
            <ChevronDown size={16} className="transition-transform group-open:rotate-180" aria-hidden />
            Motion recording
          </summary>
          {recordingOpen && <div className="pt-3"><MotionRecordingSettings /></div>}
        </details>
      )}

      {selected && (
        <CameraViewer camera={selected} token={token} view={view} onView={setView} onClose={close} />
      )}
    </div>
  );
};

export default Cameras;
