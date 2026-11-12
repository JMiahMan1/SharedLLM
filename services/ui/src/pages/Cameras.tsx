import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  BatteryMedium, CameraOff, Cctv, ChevronLeft, ChevronRight, Film, Loader2, Moon, Play, RefreshCw, X,
} from 'lucide-react';
import { api } from '../services/api';
import { useMediaToken } from '../lib/mediaToken';
import { useHaptics } from '../hooks/useHaptics';
import { errorText } from '../lib/errorText';
import {
  addDays, cameraMediaUrl, dayRange, formatClock, formatDuration, fromDateInput, isCameraOnline, sameDay,
  tileRefreshMs, toDateInput, triggerLabel, triggerStyle,
  type CameraInfo, type CameraRecording,
} from '../lib/cameras';

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
        className="relative block w-full aspect-video bg-slate-900/80 text-left"
        aria-label={`Open ${name}`}
      >
        {src && !failed && (
          <img
            src={src}
            alt=""
            className={`absolute inset-0 h-full w-full object-cover transition-opacity ${loaded ? 'opacity-100' : 'opacity-0'}`}
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
  const online = isCameraOnline(camera);

  // Every frame keeps a battery camera awake, so its live view stops on its own.
  useEffect(() => {
    if (!camera.battery || paused) return undefined;
    const id = window.setTimeout(() => setPaused(true), BATTERY_LIVE_LIMIT_MS);
    return () => window.clearTimeout(id);
  }, [camera.battery, paused, attempt]);

  const src = online && !paused ? cameraMediaUrl(token, camera.entity_id, 'stream', { n: String(attempt) }) : null;
  const again = () => { setFailed(false); setLoaded(false); setPaused(false); setAttempt((a) => a + 1); };

  return (
    <div className="space-y-2">
      <div className="relative w-full aspect-video rounded-xl overflow-hidden bg-black">
        {src && !failed && (
          <img
            key={src}
            src={src}
            alt={`${displayName(camera)} live`}
            className="absolute inset-0 h-full w-full object-contain"
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
        {camera.stream === 'camera'
          ? 'The camera’s own video.'
          : `A fresh picture about every ${Math.max(0.5, camera.snapshot_interval ?? 1)} s.`}
        {camera.battery ? ' Battery camera: live view stops after two minutes.' : ''}
      </p>
    </div>
  );
}

function RecordingsView({ camera, token }: { camera: CameraInfo; token: ReturnType<typeof useMediaToken> }) {
  const [day, setDay] = useState(() => new Date());
  const [playing, setPlaying] = useState<CameraRecording | null>(null);
  const [playError, setPlayError] = useState(false);
  const today = new Date();
  const isToday = sameDay(day, today);
  const range = useMemo(() => dayRange(day), [day]);

  const recordings = useQuery({
    queryKey: ['camera-recordings', camera.entity_id, toDateInput(day)],
    queryFn: () => api.getCameraRecordings(camera.entity_id, range.start, range.end),
    // Today's list grows while you watch; past days do not change.
    refetchInterval: () => (isToday && !document.hidden ? 60_000 : false),
  });
  const items = recordings.data?.recordings ?? [];

  const choose = (rec: CameraRecording) => { setPlayError(false); setPlaying(rec); };
  const playUrl = playing ? cameraMediaUrl(token, camera.entity_id, 'play', { file: playing.file }) : null;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setDay((d) => addDays(d, -1))}
          className={`${TOUCH} flex items-center justify-center rounded-xl bg-white/5 border border-white/10 text-white hover:bg-white/10`}
          aria-label="Previous day"
        >
          <ChevronLeft size={18} />
        </button>
        <input
          type="date"
          value={toDateInput(day)}
          max={toDateInput(today)}
          onChange={(e) => { const next = fromDateInput(e.target.value); if (next && next <= today) setDay(next); }}
          className="min-h-11 flex-1 min-w-0 rounded-xl bg-white/5 border border-white/10 px-3 text-sm text-white [color-scheme:dark]"
          aria-label="Day"
        />
        <button
          type="button"
          onClick={() => setDay((d) => addDays(d, 1))}
          disabled={isToday}
          className={`${TOUCH} flex items-center justify-center rounded-xl bg-white/5 border border-white/10 text-white hover:bg-white/10 disabled:opacity-40`}
          aria-label="Next day"
        >
          <ChevronRight size={18} />
        </button>
      </div>

      {playing && (
        <div className="space-y-1">
          {playUrl && !playError ? (
            <video
              key={playing.file}
              src={playUrl}
              controls
              autoPlay
              playsInline
              preload="metadata"
              className="w-full aspect-video rounded-xl bg-black"
              onError={() => setPlayError(true)}
              aria-label={`Recording at ${formatClock(playing.start)}`}
            />
          ) : (
            <div className="w-full aspect-video rounded-xl bg-black flex flex-col items-center justify-center gap-2 text-center p-4">
              <span className="text-sm text-slate-300">{playError ? 'This recording would not play.' : 'Getting ready…'}</span>
              {playError && (
                <button type="button" onClick={() => setPlayError(false)} className={`${TOUCH} px-4 text-sm text-cyan-300 underline`}>
                  Try again
                </button>
              )}
            </div>
          )}
          <p className="text-xs text-slate-400">
            {formatClock(playing.start)} · {formatDuration(playing.duration)}
          </p>
        </div>
      )}

      {recordings.isLoading ? (
        <div className="flex justify-center py-8" role="status" aria-label="Loading recordings">
          <Loader2 className="animate-spin text-slate-500" />
        </div>
      ) : recordings.isError ? (
        <div className="text-center py-6">
          <p className="text-sm text-red-300">{errorText(recordings.error, 'Couldn’t read the recordings')}</p>
          <button type="button" onClick={() => void recordings.refetch()} className={`${TOUCH} px-4 text-sm text-cyan-300 underline`}>
            Try again
          </button>
        </div>
      ) : items.length === 0 ? (
        <p className="text-sm text-slate-500 py-6 text-center">No recordings {isToday ? 'yet today' : 'on this day'}.</p>
      ) : (
        <ul className="space-y-1.5" aria-label="Recordings">
          {items.map((rec) => {
            const selected = playing?.file === rec.file;
            return (
              <li key={rec.file}>
                <button
                  type="button"
                  onClick={() => choose(rec)}
                  aria-pressed={selected}
                  className={`w-full min-h-12 flex items-center gap-3 rounded-xl border px-3 py-2 text-left transition-colors ${
                    selected ? 'bg-cyan-500/15 border-cyan-500/40' : 'bg-white/5 border-white/10 hover:bg-white/10'
                  }`}
                >
                  <Play size={16} className="text-cyan-300 shrink-0" aria-hidden />
                  <span className="text-sm text-white tabular-nums shrink-0">{formatClock(rec.start)}</span>
                  <span className="text-xs text-slate-400 tabular-nums shrink-0">{formatDuration(rec.duration)}</span>
                  <span className="flex flex-wrap gap-1 min-w-0 justify-end ml-auto">
                    {rec.triggers.map((t) => (
                      <span key={t} className={`rounded-full border px-2 py-0.5 text-[11px] ${triggerStyle(t)}`}>{triggerLabel(t)}</span>
                    ))}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
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
      <div className="mx-auto w-full max-w-4xl px-3 sm:px-6 pb-10 pt-[max(0.75rem,env(safe-area-inset-top))] space-y-3">
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold text-white truncate">{displayName(camera)}</h2>
            <p className="text-xs text-slate-400 truncate">{[camera.area, camera.model].filter(Boolean).join(' · ')}</p>
          </div>
          <CameraBadges camera={camera} />
          <button
            type="button"
            onClick={onClose}
            className={`${TOUCH} flex items-center justify-center rounded-xl bg-white/5 border border-white/10 text-white hover:bg-white/10`}
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>
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
        {tab === 'live' ? <LiveView camera={camera} token={token} /> : <RecordingsView camera={camera} token={token} />}
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
    <div className="mx-auto w-full max-w-6xl space-y-4 pb-24">
      <div className="flex items-center gap-2">
        <Cctv className="text-cyan-400" size={22} aria-hidden />
        <h1 className="text-2xl font-bold text-white">Cameras</h1>
      </div>

      {cameras.isLoading ? (
        <div className="flex justify-center py-10" role="status" aria-label="Loading">
          <Loader2 className="animate-spin text-slate-500" />
        </div>
      ) : cameras.isError ? (
        <div className="glass-panel rounded-2xl p-6 text-center">
          <p className="text-sm text-red-300">{errorText(cameras.error, 'Couldn’t reach the cameras')}</p>
          <button type="button" onClick={() => void cameras.refetch()} className={`${TOUCH} px-4 text-sm text-cyan-300 underline`}>
            Try again
          </button>
        </div>
      ) : list.length === 0 ? (
        <div className="glass-panel rounded-2xl p-6 text-center">
          <p className="text-sm text-slate-400">No cameras to show.</p>
          <p className="text-xs text-slate-500 mt-1">Cameras you are allowed to see show up here.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {list.map((camera) => (
            <CameraTile key={camera.entity_id} camera={camera} token={token} onOpen={open} />
          ))}
        </div>
      )}

      {selected && (
        <CameraViewer camera={selected} token={token} view={view} onView={setView} onClose={close} />
      )}
    </div>
  );
};

export default Cameras;
