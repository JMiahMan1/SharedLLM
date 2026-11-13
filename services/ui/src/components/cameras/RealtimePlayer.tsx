import { useEffect, useRef, useState } from 'react';
import { Loader2, Volume2, VolumeX } from 'lucide-react';
import { cameraRotation, rotatedMediaStyle, type CameraInfo } from '../../lib/cameras';
import type { MediaToken } from '../../lib/mediaToken';
import {
  browserCaps, firstFrameTimeoutMs, mp4Url, mseSocketUrl, nextMode, noteModeFailed, noteModeWorked, pickQuality,
  playbackModes, webrtcUrl, type LiveMode, type LiveQuality,
} from '../../lib/cameraLive';
import { startMp4, startMse, startWebrtc, type StopFn } from './liveTransports';

const TOUCH = 'min-h-11 min-w-11';
const MODE_LABEL: Record<LiveMode, string> = { webrtc: 'WebRTC', mse: 'MSE', mp4: 'MP4' };
const QUALITY_LABEL: Record<LiveQuality, string> = { sub: 'SD', main: 'HD' };

function start(mode: LiveMode, video: HTMLVideoElement, token: MediaToken, entityId: string, quality: LiveQuality,
  fail: (reason: string) => void): StopFn {
  if (mode === 'webrtc') return startWebrtc(video, webrtcUrl(token, entityId, quality), { fail });
  if (mode === 'mse') return startMse(video, mseSocketUrl(token, entityId, quality), { fail });
  return startMp4(video, mp4Url(token, entityId, quality), { fail });
}

/**
 * A camera's real-time video (go2rtc): WebRTC first, then MSE, then a plain MP4,
 * moving on when one fails or shows no picture in time. When all of them fail
 * it calls `onGiveUp` and the page shows the picture stream instead.
 *
 * Unmounting stops the stream, which is what lets go2rtc close a battery camera's
 * connection and the camera go back to sleep.
 */
export default function RealtimePlayer({ camera, token, onGiveUp }: {
  camera: CameraInfo;
  token: MediaToken | null;
  onGiveUp: (reason: string) => void;
}) {
  const realtime = camera.realtime ?? null;
  // Once per mount (the list refetches every 30 s and must not restart the stream); a mode that fails
  // here goes to the back of the list for the next camera opened.
  const [modes] = useState(() => playbackModes(realtime, browserCaps()));
  const [mode, setMode] = useState<LiveMode | null>(modes[0] ?? null);
  const [quality, setQuality] = useState<LiveQuality>(() => (realtime ? pickQuality(realtime) : 'sub'));
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(true);
  const [reason, setReason] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const tokenRef = useRef(token);
  const giveUpRef = useRef(onGiveUp);
  useEffect(() => { tokenRef.current = token; giveUpRef.current = onGiveUp; }, [token, onGiveUp]);
  const hasToken = Boolean(token);

  useEffect(() => {
    if (!mode) giveUpRef.current('No real-time video for this camera');
  }, [mode]);

  useEffect(() => {
    const video = videoRef.current;
    const held = tokenRef.current;
    if (!mode || !video || !held || !hasToken) return undefined;
    // A new stream is starting; the old one's picture is gone.
    setPlaying(false);
    let over = false;
    const fail = (why: string) => {
      if (over) return;
      over = true;
      noteModeFailed(mode);
      setReason(why);
      const next = nextMode(modes, mode);
      if (next) setMode(next);
      else giveUpRef.current(why);
    };
    const timer = window.setTimeout(() => fail('No picture from the camera'), firstFrameTimeoutMs(camera.battery));
    const onPlaying = () => {
      window.clearTimeout(timer);
      noteModeWorked(mode);
      setPlaying(true);
    };
    video.addEventListener('playing', onPlaying);
    const stop = start(mode, video, held, camera.entity_id, quality, fail);
    // Autoplay waits for media; 'playing' fires when it comes. Some engines return no promise.
    const played = video.play() as Promise<void> | undefined;
    played?.catch?.(() => {});
    return () => {
      over = true;
      window.clearTimeout(timer);
      video.removeEventListener('playing', onPlaying);
      stop();
    };
  }, [mode, quality, hasToken, camera.entity_id, camera.battery, modes]);

  const qualities = realtime?.qualities ?? [];
  return (
    <div className="relative w-full aspect-video rounded-xl overflow-hidden bg-black">
      <video
        ref={videoRef}
        className="absolute inset-0 h-full w-full object-contain"
        style={rotatedMediaStyle(cameraRotation(camera))}
        autoPlay
        playsInline
        muted={muted}
        aria-label={`${camera.name || camera.entity_id} live`}
      />
      {!playing && (
        <span className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-slate-400" role="status">
          <Loader2 className="animate-spin" size={22} aria-hidden />
          <span className="text-xs">{camera.battery ? 'Waking the camera…' : 'Connecting…'}</span>
          {reason && <span className="text-[11px] text-slate-500">Trying another way ({reason})</span>}
        </span>
      )}
      {playing && (
        <span className="absolute left-2 top-2 flex items-center gap-1.5 rounded-full bg-black/60 px-2 py-0.5 text-[11px] font-semibold text-red-300">
          <span className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" aria-hidden />LIVE
          {mode && <span className="font-normal text-slate-300">{MODE_LABEL[mode]}</span>}
        </span>
      )}
      <span className="absolute right-1 bottom-1 flex items-center gap-1">
        {qualities.length > 1 && (
          <span className="flex rounded-xl bg-black/60 p-0.5" role="group" aria-label="Quality">
            {qualities.map((q) => (
              <button
                key={q}
                type="button"
                aria-pressed={q === quality}
                onClick={() => setQuality(q)}
                className={`${TOUCH} rounded-lg px-2 text-xs font-semibold ${q === quality ? 'bg-cyan-500/30 text-cyan-100' : 'text-slate-300'}`}
              >
                {QUALITY_LABEL[q]}
              </button>
            ))}
          </span>
        )}
        <button
          type="button"
          onClick={() => setMuted((m) => !m)}
          className={`${TOUCH} flex items-center justify-center rounded-xl bg-black/60 text-slate-200`}
          aria-label={muted ? 'Turn sound on' : 'Turn sound off'}
        >
          {muted ? <VolumeX size={16} /> : <Volume2 size={16} />}
        </button>
      </span>
    </div>
  );
}
