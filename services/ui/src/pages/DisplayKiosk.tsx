import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle, Camera, CameraOff, ChevronDown, Clock, Cloud, Mic, MicOff,
  Pause, Phone, PhoneOff, Play, Square, Video, VideoOff, Volume2, X,
} from 'lucide-react';
import { api } from '../services/api';
import { useCall, type CallInfo } from '../context/CallContext';
import { useHaptics } from '../hooks/useHaptics';
import { useWeather } from '../hooks/useWeather';
import { cameraMediaUrl, type CameraInfo } from '../lib/cameras';
import { useMediaToken } from '../lib/mediaToken';
import { extractEchoFleet, type EchoDeviceSummary, type EchoState } from '../lib/echoFleet';
import type { ActiveCall, TimerRecord } from '../types/api';

/** 44px on touch screens without inflating the 1280x800 kiosk layout. */
const TOUCH = 'min-h-11 pointer-coarse:min-h-11';
/** Call controls stay thumb-sized even on the Show's landscape bar. */
const CALL_BUTTON = `${TOUCH} min-h-12 pointer-coarse:min-h-12`;
const STATES_KEY = ['display-kiosk', 'states'];
const TIMERS_KEY = ['display-kiosk', 'timers'];

function messageError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Shown on the Drop-In bar only: viewing hub cameras must not probe the Show webcam. */
function cameraErrorMessage(error?: unknown): string {
  const reason = error === undefined ? '' : ` ${messageError(error)}.`;
  return `Camera unavailable.${reason} Open the privacy shutter and allow camera access.`;
}

function formatDate(date: Date, options: Intl.DateTimeFormatOptions, zone?: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, { ...options, ...(zone ? { timeZone: zone } : {}) }).format(date);
  } catch {
    // Say which setting is wrong; never quietly pretend it is the local zone.
    return `Invalid time zone: ${zone}`;
  }
}

function callRoom(target: CallInfo | ActiveCall): string {
  return ('room' in target && target.room) ? target.room : target.token;
}

function CameraPopup({ onClose }: { onClose: () => void }) {
  const token = useMediaToken();
  // These are hub/Reolink streams. Never call getUserMedia here: it switches on
  // the Show's own front webcam (and its LED) just to view the front door.
  const camerasQuery = useQuery({
    queryKey: ['display-kiosk', 'cameras'],
    queryFn: () => api.getCameras(),
    retry: 1,
  });

  const cameras: CameraInfo[] = camerasQuery.data ?? [];

  return (
    <div
      role="dialog"
      aria-label="Cameras"
      data-testid="camera-popup"
      className="fixed inset-0 z-[150] flex items-center justify-center bg-slate-950/80 p-3 sm:p-6"
    >
      <div className="w-full max-w-4xl rounded-2xl border border-slate-700 bg-slate-900 p-4 text-slate-100 shadow-2xl sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-lg font-semibold"><Camera size={19} aria-hidden /> Cameras &amp; doorbells</h2>
          <button
            type="button"
            onClick={onClose}
            className={`${TOUCH} flex items-center gap-2 rounded-xl bg-slate-700 px-4 text-sm font-semibold text-white hover:bg-slate-600`}
          >
            <X size={16} aria-hidden /> Dismiss
          </button>
        </div>

        {camerasQuery.isPending && <p role="status" className="mt-3 text-sm text-slate-300">Loading cameras from Home Hub…</p>}
        {camerasQuery.isError && (
          <p role="alert" className="mt-3 flex items-start gap-2 rounded-xl border border-red-500/40 bg-red-950/50 p-3 text-sm text-red-100">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
            Cameras unavailable: {messageError(camerasQuery.error)}
          </p>
        )}
        {!camerasQuery.isPending && !camerasQuery.isError && cameras.length === 0 && (
          <p role="status" className="mt-3 rounded-xl bg-slate-800 p-3 text-sm text-slate-300">
            No cameras are shared by Home Hub. Adopt a camera before using the doorbell view.
          </p>
        )}
        {cameras.length > 0 && (
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
            {cameras.map((camera) => {
              const snapshot = cameraMediaUrl(token, camera.entity_id, 'snapshot');
              return (
                <div key={camera.entity_id} className="min-w-[280px] rounded-xl bg-slate-800/70 p-3">
                  <h3 className="truncate text-sm font-semibold">{camera.name}</h3>
                  {camera.state === 'unavailable' && (
                    <p role="alert" className="mt-1 text-xs text-amber-300">This camera is offline in Home Hub.</p>
                  )}
                  {snapshot ? (
                    <img src={snapshot} alt={`${camera.name} live view`} className="mt-2 aspect-video w-full rounded-lg bg-slate-950 object-cover" />
                  ) : (
                    <p role="status" className="mt-2 text-xs text-slate-300">Waiting for a signed camera token…</p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Full-screen-ish smart display for an Echo Show 8 (project `crown`, browser
 * pid `kiosk-satellite`). hassmic still owns wake word and audio; this page is
 * the glass. It reads every value from the hub/weather APIs and names any
 * missing integration rather than painting a plausible fake reading.
 */
export default function DisplayKiosk() {
  const weather = useWeather();
  const { call, activeCalls, muted, cameraOn, startCall, endCall, toggleMute, toggleCamera } = useCall();
  const haptics = useHaptics();
  const queryClient = useQueryClient();

  const [now, setNow] = useState(() => new Date());
  const [camerasOpen, setCamerasOpen] = useState(false);
  const [roomsOpen, setRoomsOpen] = useState(false);
  const [dismissedVoice, setDismissedVoice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [cameraError, setCameraError] = useState<string | null>(null);

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const statesQuery = useQuery({
    queryKey: STATES_KEY,
    queryFn: () => api.getEntities(),
    retry: 1,
  });
  const timersQuery = useQuery({
    queryKey: TIMERS_KEY,
    queryFn: () => api.getTimers(),
    refetchInterval: 10_000,
    retry: 1,
  });

  const states = statesQuery.data ?? [];
  const fleet = useMemo(() => extractEchoFleet(states as EchoState[]), [states]);
  const timers = (timersQuery.data ?? []).filter((timer) => timer.active);
  const mediaPlayers = states.filter((state) => state.domain === 'media_player');
  const activeMedia = mediaPlayers.find((state) => state.state === 'playing') ?? mediaPlayers[0];
  // Home Hub reports volume as 0..1. Never invent a default: when it is not
  // published the slider is disabled and says so.
  const mediaVolume = activeMedia?.attributes?.volume_level;
  const reportedVolume = typeof mediaVolume === 'number'
    ? Math.max(0, Math.min(100, Math.round(mediaVolume * 100)))
    : null;
  const quickToggles = states
    .filter((state) => (state.domain === 'light' || state.domain === 'switch') && state.state !== 'unavailable')
    .slice(0, 6);

  const voiceDevice: EchoDeviceSummary | undefined = fleet.find((device) => device.lastHeard || device.lastReply);
  const voiceKey = voiceDevice
    ? `${voiceDevice.node}:${voiceDevice.lastHeard ?? ''}:${voiceDevice.lastReply ?? ''}`
    : null;
  const showVoice = Boolean(voiceKey && voiceKey !== dismissedVoice);
  const zone = weather.data?.timezone ?? undefined;
  const time = formatDate(now, { hour: 'numeric', minute: '2-digit' }, zone);
  const date = formatDate(now, { weekday: 'long', month: 'long', day: 'numeric' }, zone);

  const reportFailure = (label: string, error: unknown) => {
    setActionError(`${label}: ${messageError(error)}`);
  };

  /** A command changes hub state; refetch both the entity and timer snapshots. */
  const refreshKioskData = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: STATES_KEY }),
      queryClient.invalidateQueries({ queryKey: TIMERS_KEY }),
    ]);
  };

  const stopTimer = async (timer: TimerRecord) => {
    setActionError(null);
    try {
      const type = timer.type === 'alarm' || timer.type === 'reminder' ? timer.type : 'timer';
      await api.timerAction('stop', { title: timer.title, type, id: timer.id });
      await refreshKioskData();
      void haptics.trigger('medium');
    } catch (error) {
      reportFailure(`Could not stop ${timer.title}`, error);
    }
  };

  const controlMedia = async (service: 'media_play' | 'media_pause' | 'volume_set', data?: Record<string, unknown>) => {
    if (!activeMedia) return;
    setActionError(null);
    try {
      await api.callHaService('media_player', service, activeMedia.entity_id, data ?? null);
      await refreshKioskData();
      void haptics.trigger('light');
    } catch (error) {
      reportFailure(`Could not control ${activeMedia.friendly_name}`, error);
    }
  };

  const toggleRoom = async (entityId: string, isOn: boolean) => {
    setActionError(null);
    try {
      await api.callHaService(entityId.split('.', 1)[0] ?? '', isOn ? 'turn_off' : 'turn_on', entityId);
      await refreshKioskData();
      void haptics.trigger('light');
    } catch (error) {
      reportFailure(`Could not switch ${entityId}`, error);
    }
  };

  const answer = async (video: boolean) => {
    const target = call ?? activeCalls[0];
    if (!target) {
      setActionError('No incoming Drop-In call is available to answer.');
      return;
    }
    setActionError(null);
    setCameraError(null);
    if (video && !navigator.mediaDevices?.getUserMedia) {
      setCameraError(cameraErrorMessage());
      return;
    }
    try {
      await startCall(callRoom(target), target.name || 'Family call', { video, private: target.private });
      void haptics.trigger('medium');
    } catch (error) {
      if (video) setCameraError(cameraErrorMessage(error));
      else reportFailure('Could not answer the Drop-In', error);
    }
  };

  const handleToggleCamera = async () => {
    setCameraError(null);
    if (!cameraOn && !navigator.mediaDevices?.getUserMedia) {
      setCameraError(cameraErrorMessage());
      return;
    }
    try {
      await toggleCamera();
      void haptics.trigger('light');
    } catch (error) {
      setCameraError(cameraErrorMessage(error));
    }
  };

  const dropInto = async (device: EchoDeviceSummary) => {
    if (!device.satelliteEntityId && !device.dropInEntityId) {
      setActionError(`Drop-In is unavailable for ${device.name}: Home Hub has no satellite or drop-in entity for it.`);
      return;
    }
    setActionError(null);
    try {
      await startCall(device.node, device.name, { video: false });
      void haptics.trigger('medium');
      setRoomsOpen(false);
    } catch (error) {
      reportFailure(`Could not drop in on ${device.name}`, error);
    }
  };

  const currentTemp = weather.data?.current.temp;
  const temperature = currentTemp === null || currentTemp === undefined
    ? 'temperature not reported'
    : `${Math.round(currentTemp)}${weather.data?.units.temp ?? ''}`;

  return (
    <div className="min-h-dvh w-full bg-slate-950 px-3 pb-[calc(var(--safe-bottom)+1.5rem)] pt-[calc(var(--safe-top)+1rem)] text-slate-100 sm:px-5 lg:px-8">
      <div className="mx-auto flex max-w-[1600px] flex-col gap-4 sm:gap-5">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-indigo-300">Jarvis · smart display</p>
            <p data-testid="kiosk-clock" className="text-4xl font-light tabular-nums text-white sm:text-5xl lg:text-6xl">{time}</p>
            <p data-testid="kiosk-date" className="text-sm text-slate-300 sm:text-base">{date}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setCamerasOpen(true)}
              aria-label="Cameras"
              className={`${TOUCH} flex items-center gap-2 rounded-xl border border-slate-700 bg-slate-800 px-4 text-sm font-semibold text-slate-100 hover:bg-slate-700`}
            >
              <Camera size={17} aria-hidden /> Cameras
            </button>
            <button
              type="button"
              onClick={() => setRoomsOpen((open) => !open)}
              aria-expanded={roomsOpen}
              aria-label="Drop In rooms"
              className={`${TOUCH} flex items-center gap-2 rounded-xl bg-indigo-500 px-4 text-sm font-semibold text-white hover:bg-indigo-400`}
            >
              <Phone size={17} aria-hidden /> Drop In rooms <ChevronDown size={15} aria-hidden />
            </button>
          </div>
        </header>

        {actionError && (
          <p role="alert" className="flex items-start gap-2 rounded-xl border border-red-500/40 bg-red-950/60 p-3 text-sm text-red-100">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden /> {actionError}
          </p>
        )}
        {statesQuery.isError && (
          <p role="alert" className="flex items-start gap-2 rounded-xl border border-red-500/40 bg-red-950/60 p-3 text-sm text-red-100">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
            Home Hub is unavailable: {messageError(statesQuery.error)}. Drop-In rooms and quick controls cannot be shown.
          </p>
        )}

        <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <section data-testid="kiosk-weather" className="min-w-[280px] rounded-2xl border border-slate-700/70 bg-slate-900/80 p-4">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-300"><Cloud size={17} aria-hidden /> Weather</h2>
            {weather.isPending && <p role="status" className="mt-2 text-sm text-slate-300">Loading forecast…</p>}
            {weather.isError && (
              <p role="alert" className="mt-2 text-sm text-red-200">
                Forecast unavailable: {messageError(weather.error)}
              </p>
            )}
            {weather.data && (
              <div className="mt-2">
                <p className="text-3xl font-light text-white">{temperature}</p>
                <p className="text-sm text-slate-200">{weather.data.current.label || 'condition not reported'}</p>
                <p className="mt-1 text-xs text-slate-400">{weather.data.location.name}</p>
              </div>
            )}
          </section>

          <section data-testid="active-timers" className="min-w-[280px] rounded-2xl border border-slate-700/70 bg-slate-900/80 p-4 sm:col-span-2">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-300"><Clock size={17} aria-hidden /> Active timers</h2>
            {timersQuery.isPending && <p role="status" className="mt-2 text-sm text-slate-300">Loading timers…</p>}
            {timersQuery.isError && (
              <p role="alert" className="mt-2 text-sm text-red-200">Timers unavailable: {messageError(timersQuery.error)}</p>
            )}
            {!timersQuery.isPending && !timersQuery.isError && timers.length === 0 && (
              <p role="status" className="mt-2 text-sm text-slate-400">No active timers.</p>
            )}
            {timers.length > 0 && (
              <ul className="mt-3 flex flex-wrap gap-3">
                {timers.map((timer) => (
                  <li key={timer.id} className={`${TOUCH} flex items-center gap-3 rounded-xl bg-slate-800 px-3`}>
                    <span className="text-sm font-semibold text-white">{timer.title}</span>
                    <button
                      type="button"
                      aria-label={`Stop ${timer.title}`}
                      onClick={() => void stopTimer(timer)}
                      className={`${TOUCH} flex items-center gap-2 rounded-lg bg-red-500 px-3 text-sm font-bold text-white hover:bg-red-400`}
                    >
                      <Square size={15} aria-hidden /> Stop
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section data-testid="now-playing" className="min-w-[280px] rounded-2xl border border-slate-700/70 bg-slate-900/80 p-4">
            <h2 className="text-sm font-semibold text-slate-300">Now Playing</h2>
            {activeMedia ? (
              <>
                <p className="mt-2 truncate text-lg font-semibold text-white">{activeMedia.friendly_name}</p>
                <p className="text-xs text-slate-400">state: {activeMedia.state}</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    aria-label="Play music"
                    onClick={() => void controlMedia('media_play')}
                    className={`${TOUCH} flex items-center gap-2 rounded-xl bg-indigo-500 px-4 text-sm font-semibold text-white hover:bg-indigo-400`}
                  ><Play size={16} aria-hidden /> Play</button>
                  <button
                    type="button"
                    aria-label="Pause music"
                    onClick={() => void controlMedia('media_pause')}
                    className={`${TOUCH} flex items-center gap-2 rounded-xl bg-slate-700 px-4 text-sm font-semibold text-white hover:bg-slate-600`}
                  ><Pause size={16} aria-hidden /> Pause</button>
                </div>
                <label className="mt-3 block text-xs text-slate-300">
                  <span className="flex items-center gap-2"><Volume2 size={15} aria-hidden /> Volume</span>
                  {reportedVolume === null ? (
                    <>
                      <input
                        type="range"
                        min={0}
                        max={100}
                        value={0}
                        disabled
                        aria-label="Volume"
                        className={`${TOUCH} w-full accent-indigo-400 disabled:opacity-50`}
                      />
                      <span role="status" className="mt-1 block text-[11px] text-amber-300">Volume not reported by Home Hub.</span>
                    </>
                  ) : (
                    <input
                      key={`${activeMedia?.entity_id ?? 'media'}:${reportedVolume}`}
                      type="range"
                      min={0}
                      max={100}
                      defaultValue={reportedVolume}
                      aria-label="Volume"
                      onChange={(event) => void controlMedia('volume_set', { volume_level: Number(event.target.value) / 100 })}
                      className={`${TOUCH} w-full accent-indigo-400`}
                    />
                  )}
                </label>
              </>
            ) : (
              <p role="status" className="mt-2 text-sm text-slate-400">
                No media player entities are published by Home Hub.
              </p>
            )}
          </section>

          <section data-testid="quick-rooms" className="min-w-[280px] rounded-2xl border border-slate-700/70 bg-slate-900/80 p-4">
            <h2 className="text-sm font-semibold text-slate-300">Quick room controls</h2>
            {quickToggles.length === 0 ? (
              <p role="status" className="mt-2 text-sm text-slate-400">No switchable room entities are published by Home Hub.</p>
            ) : (
              <ul className="mt-3 grid grid-cols-1 gap-2">
                {quickToggles.map((entity) => (
                  <li key={entity.entity_id} className={`${TOUCH} flex items-center justify-between gap-3 rounded-xl bg-slate-800 px-3`}>
                    <span className="truncate text-sm">{entity.friendly_name}</span>
                    <button
                      type="button"
                      aria-label={`Toggle ${entity.friendly_name}`}
                      onClick={() => void toggleRoom(entity.entity_id, entity.state === 'on')}
                      className={`${TOUCH} rounded-lg bg-slate-600 px-3 text-xs font-semibold text-white hover:bg-slate-500`}
                    >
                      {entity.state === 'on' ? 'On' : 'Off'}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section
            data-testid="dropin-bar"
            aria-label="Audio and video Drop-In"
            className="min-w-[280px] rounded-2xl border border-indigo-500/30 bg-indigo-950/40 p-4 sm:col-span-2"
          >
            <h2 className="flex items-center gap-2 text-sm font-semibold text-indigo-200"><Phone size={17} aria-hidden /> Audio &amp; video Drop-In</h2>
            {cameraError && (
              <p role="alert" className="mt-2 flex items-start gap-2 rounded-xl border border-amber-500/40 bg-amber-950/40 p-3 text-sm text-amber-100">
                <CameraOff size={16} className="mt-0.5 shrink-0" aria-hidden /> {cameraError}
              </p>
            )}
            {call && <p className="mt-1 text-xs text-indigo-200">{call.name} · {call.status}</p>}
            {activeCalls.map((active) => (
              <p key={active.token} className="mt-1 truncate text-sm text-white">
                {active.caller ? `${active.caller} → ` : ''}{active.name}
                {active.video ? ' · video' : ' · audio'}
              </p>
            ))}
            {activeCalls.length === 0 && !call && (
              <p role="status" className="mt-2 text-sm text-indigo-200">No active Drop-In calls.</p>
            )}
            <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-5">
              <button
                type="button"
                onClick={() => void answer(false)}
                className={`${CALL_BUTTON} flex items-center justify-center gap-2 rounded-xl bg-emerald-500 px-3 text-sm font-bold text-emerald-950 hover:bg-emerald-400`}
              ><Phone size={17} aria-hidden /> Answer (Audio)</button>
              <button
                type="button"
                onClick={() => void answer(true)}
                className={`${CALL_BUTTON} flex items-center justify-center gap-2 rounded-xl bg-sky-500 px-3 text-sm font-bold text-white hover:bg-sky-400`}
              ><Video size={17} aria-hidden /> Answer (Video)</button>
              <button
                type="button"
                onClick={toggleMute}
                className={`${CALL_BUTTON} flex items-center justify-center gap-2 rounded-xl bg-slate-700 px-3 text-sm font-semibold text-white hover:bg-slate-600`}
              >{muted ? <MicOff size={17} aria-hidden /> : <Mic size={17} aria-hidden />} {muted ? 'Unmute' : 'Mute'}</button>
              <button
                type="button"
                onClick={() => void handleToggleCamera()}
                className={`${CALL_BUTTON} flex items-center justify-center gap-2 rounded-xl bg-slate-700 px-3 text-sm font-semibold text-white hover:bg-slate-600`}
              >{cameraOn ? <VideoOff size={17} aria-hidden /> : <Camera size={17} aria-hidden />} Camera</button>
              <button
                type="button"
                onClick={endCall}
                className={`${CALL_BUTTON} flex items-center justify-center gap-2 rounded-xl bg-red-500 px-3 text-sm font-bold text-white hover:bg-red-400`}
              ><PhoneOff size={17} aria-hidden /> End Call</button>
            </div>
          </section>
        </div>

        {roomsOpen && (
          <section data-testid="dropin-rooms" className="rounded-2xl border border-slate-700 bg-slate-900 p-4" aria-label="Drop In rooms">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-slate-200">Drop In on an Echo room</h2>
              <button
                type="button"
                onClick={() => setRoomsOpen(false)}
                aria-label="Close Drop In rooms"
                className={`${TOUCH} rounded-lg bg-slate-700 px-3 text-xs font-semibold text-white`}
              >Close</button>
            </div>
            {fleet.length === 0 ? (
              <p role="status" className="mt-2 text-sm text-slate-400">No Echo rooms are published by Home Hub.</p>
            ) : (
              <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {fleet.map((device) => (
                  <li key={device.node} className={`${TOUCH} flex items-center justify-between gap-3 rounded-xl bg-slate-800 px-3`}>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-white">{device.name}</p>
                      <p className="truncate text-[11px] text-slate-400">{device.dropInState}</p>
                    </div>
                    <button
                      type="button"
                      aria-label={`Drop In on ${device.name}`}
                      onClick={() => void dropInto(device)}
                      className={`${TOUCH} shrink-0 rounded-lg bg-indigo-500 px-3 text-xs font-bold text-white hover:bg-indigo-400`}
                    >Drop In</button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {showVoice && voiceDevice && voiceKey && (
          <div
            data-testid="voice-overlay"
            role="status"
            onClick={() => setDismissedVoice(voiceKey)}
            className="fixed bottom-[calc(var(--safe-bottom)+1rem)] left-1/2 z-[140] w-[min(94vw,64rem)] -translate-x-1/2 cursor-pointer rounded-2xl border-2 border-white bg-slate-950 p-4 text-white shadow-2xl"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs font-bold uppercase tracking-widest text-emerald-300">Listening · {voiceDevice.name}</p>
                {voiceDevice.lastHeard && (
                  <p data-testid="voice-transcript" className="mt-1 text-lg font-semibold">“{voiceDevice.lastHeard}”</p>
                )}
                {voiceDevice.lastReply && (
                  <p data-testid="voice-reply" className="mt-1 text-base text-amber-200">Jarvis: {voiceDevice.lastReply}</p>
                )}
              </div>
              <button
                type="button"
                aria-label="Dismiss voice transcript"
                onClick={() => setDismissedVoice(voiceKey)}
                className={`${TOUCH} flex shrink-0 items-center gap-2 rounded-xl bg-white px-3 text-sm font-bold text-slate-950`}
              ><X size={16} aria-hidden /> Dismiss</button>
            </div>
          </div>
        )}

        {camerasOpen && <CameraPopup onClose={() => setCamerasOpen(false)} />}
      </div>
    </div>
  );
}
