import { useCallback, useEffect, useRef, useState } from 'react';
import { Camera, Loader2, MicOff, RefreshCw, RotateCcw, SendHorizontal, Square, Video, X } from 'lucide-react';
import { formatDuration, RECORD_LIMIT_S, RECORDER_AUDIO_BPS, RECORDER_VIDEO_BPS, baseMime, recorderMimeType, recorderMustStop } from '../../lib/privateVideoMedia';

type Facing = 'environment' | 'user';
type Stage = 'ready' | 'recording' | 'review';

/** What the recorder hands the chat: the clip in memory, its type and the length its clock measured. */
export interface RecordedClip {
  blob: Blob;
  mime: string;
  duration: number;
  takenAt: number;
}

/**
 * The private chat's video recorder. Like the camera (PrivateCamera) it never
 * opens the phone's camera app or a capture input, either of which can keep a
 * copy in the phone's gallery: the clip is recorded from the camera stream
 * with MediaRecorder into memory, previewed from an in-memory object URL, and
 * handed to the chat, which encrypts it before anything leaves the device.
 * Record, stop, watch it back, then send or record again. It stops by itself
 * at three minutes (RECORD_LIMIT_S), with the time on screen all along.
 */
export default function PrivateVideoRecorder({
  onSend,
  onClose,
  onPhoto,
}: {
  /** Encrypt and send the clip; `progress` reports the pieces sent. */
  onSend: (clip: RecordedClip, progress: (done: number, count: number) => void) => Promise<void>;
  onClose: () => void;
  /** Back to taking a photo (the camera), when the chat offers it. */
  onPhoto?: () => void;
}) {
  const liveRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const partsRef = useRef<Blob[]>([]);
  const bytesRef = useRef(0);
  const startedRef = useRef(0);
  const takenAtRef = useRef(0);
  const [facing, setFacing] = useState<Facing>('environment');
  const [stage, setStage] = useState<Stage>('ready');
  const [ready, setReady] = useState(false);
  const [noSound, setNoSound] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [clip, setClip] = useState<RecordedClip | null>(null);
  const [reviewUrl, setReviewUrl] = useState('');
  const [sending, setSending] = useState<{ done: number; count: number } | null>(null);
  const [error, setError] = useState('');
  const [stoppedBy, setStoppedBy] = useState<'time' | 'size' | null>(null);

  const stopTracks = useCallback(() => {
    for (const track of streamRef.current?.getTracks() ?? []) track.stop();
    streamRef.current = null;
    if (liveRef.current) liveRef.current.srcObject = null;
  }, []);

  // The camera (and microphone) while there is no clip to review; recording keeps the stream it has.
  const reviewing = stage === 'review';
  useEffect(() => {
    if (reviewing) return;
    let live = true;
    const media = navigator.mediaDevices;
    if (!media?.getUserMedia || typeof MediaRecorder === 'undefined') {
      window.setTimeout(() => live && setError('Video recording is not available here.'), 0);
      return;
    }
    const video = { facingMode: { ideal: facing }, width: { ideal: 1280 }, height: { ideal: 720 } };
    void media
      .getUserMedia({ video, audio: true })
      .then((stream) => ({ stream, sound: true }))
      // No microphone (or not allowed): a silent clip is still a clip, and the screen says so.
      .catch(() => media.getUserMedia({ video, audio: false }).then((stream) => ({ stream, sound: false })))
      .then(async ({ stream, sound }) => {
        if (!live) {
          for (const track of stream.getTracks()) track.stop();
          return;
        }
        streamRef.current = stream;
        setNoSound(!sound);
        const el = liveRef.current;
        if (el) {
          el.srcObject = stream;
          await el.play().catch(() => {});
        }
        setError('');
        setReady(true);
      })
      .catch(() => {
        if (live) setError('Jarvis could not use the camera. Allow camera and microphone access for Jarvis and try again.');
      });
    return () => {
      live = false;
      setReady(false);
      stopTracks();
    };
  }, [facing, reviewing, stopTracks]);

  // The preview's object URL lives only while it is on screen.
  useEffect(() => {
    if (!clip) return;
    const url = URL.createObjectURL(clip.blob);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the preview URL follows the clip
    setReviewUrl(url);
    return () => {
      URL.revokeObjectURL(url);
      setReviewUrl('');
    };
  }, [clip]);

  // Leaving drops everything: the recorder, the camera, the clip.
  useEffect(
    () => () => {
      const recorder = recorderRef.current;
      recorderRef.current = null;
      if (recorder && recorder.state !== 'inactive') {
        recorder.ondataavailable = null;
        recorder.onstop = null;
        recorder.stop();
      }
      partsRef.current = [];
      stopTracks();
    },
    [stopTracks],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== 'inactive') recorder.stop();
  }, []);

  // The clock, and the limits: three minutes, or the byte limit, whichever comes first.
  useEffect(() => {
    if (stage !== 'recording') return;
    const timer = window.setInterval(() => {
      const seconds = (Date.now() - startedRef.current) / 1000;
      setElapsed(seconds);
      const why = recorderMustStop(seconds, bytesRef.current);
      if (why) {
        setStoppedBy(why);
        stop();
      }
    }, 200);
    return () => window.clearInterval(timer);
  }, [stage, stop]);

  const record = () => {
    const stream = streamRef.current;
    if (!stream || stage !== 'ready') return;
    const probe = document.createElement('video');
    const mimeType = recorderMimeType(MediaRecorder.isTypeSupported?.bind(MediaRecorder), (t) => probe.canPlayType(t));
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(stream, {
        ...(mimeType ? { mimeType } : {}),
        videoBitsPerSecond: RECORDER_VIDEO_BPS,
        audioBitsPerSecond: RECORDER_AUDIO_BPS,
      });
    } catch {
      setError('This phone cannot record video here.');
      return;
    }
    partsRef.current = [];
    bytesRef.current = 0;
    recorder.ondataavailable = (event) => {
      if (!event.data?.size) return;
      partsRef.current.push(event.data);
      bytesRef.current += event.data.size;
    };
    recorder.onstop = () => {
      const seconds = Math.min(RECORD_LIMIT_S, (Date.now() - startedRef.current) / 1000);
      const type = baseMime(recorder.mimeType || mimeType || 'video/webm');
      const blob = new Blob(partsRef.current, { type });
      partsRef.current = [];
      recorderRef.current = null;
      stopTracks();
      if (!blob.size) {
        setError('Nothing was recorded. Try again.');
        setStage('ready');
        return;
      }
      setClip({ blob, mime: type, duration: seconds, takenAt: takenAtRef.current });
      setStage('review');
    };
    recorderRef.current = recorder;
    startedRef.current = Date.now();
    takenAtRef.current = Math.floor(Date.now() / 1000);
    setElapsed(0);
    setStoppedBy(null);
    setError('');
    recorder.start(1000);
    setStage('recording');
  };

  const retake = () => {
    setClip(null);
    setStoppedBy(null);
    setElapsed(0);
    setStage('ready');
  };

  const send = async () => {
    if (!clip || sending) return;
    setSending({ done: 0, count: 0 });
    setError('');
    try {
      await onSend(clip, (done, count) => setSending({ done, count }));
      setClip(null);
      onClose();
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : 'Could not send the video.');
    } finally {
      setSending(null);
    }
  };

  const recording = stage === 'recording';
  const left = Math.max(0, RECORD_LIMIT_S - elapsed);
  return (
    <div className="fixed inset-0 z-[90] flex flex-col bg-black" role="dialog" aria-modal="true" aria-label="Record a video" data-testid="private-recorder">
      <div className="flex shrink-0 items-center gap-2 px-3 pt-[max(0.75rem,var(--safe-top))]">
        <button type="button" onClick={onClose} aria-label="Close the recorder" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-white/10 text-white">
          <X size={20} />
        </button>
        <p className="min-w-0 flex-1 truncate text-center text-sm text-slate-200">Encrypted, never saved to the phone</p>
        <span className="h-11 w-11 shrink-0" aria-hidden />
      </div>
      <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden">
        <video
          ref={liveRef}
          playsInline
          muted
          className={`max-h-full max-w-full object-contain ${stage === 'review' ? 'hidden' : ''} ${facing === 'user' ? '-scale-x-100' : ''}`}
          data-testid="private-recorder-live"
        />
        {stage === 'review' && reviewUrl && (
          <video
            src={reviewUrl}
            controls
            playsInline
            controlsList="nodownload noremoteplayback"
            disablePictureInPicture
            disableRemotePlayback
            onContextMenu={(event) => event.preventDefault()}
            className="max-h-full max-w-full object-contain"
            data-testid="private-recorder-review"
          />
        )}
        {stage !== 'review' && (
          <p
            className={`absolute left-1/2 top-3 flex -translate-x-1/2 items-center gap-2 rounded-full px-3 py-1 text-sm font-semibold tabular-nums text-white ${
              recording ? 'bg-rose-600/90' : 'bg-black/60'
            }`}
            role="timer"
            aria-live="off"
            aria-label={recording ? `Recording, ${formatDuration(elapsed)} of ${formatDuration(RECORD_LIMIT_S)}` : `Up to ${formatDuration(RECORD_LIMIT_S)}`}
            data-testid="private-recorder-timer"
          >
            {recording && <span className="h-2.5 w-2.5 rounded-full bg-white motion-safe:animate-pulse" aria-hidden />}
            {formatDuration(elapsed)} / {formatDuration(RECORD_LIMIT_S)}
          </p>
        )}
        {recording && left <= 10 && (
          <p className="absolute left-1/2 top-14 -translate-x-1/2 rounded-full bg-black/70 px-3 py-1 text-xs text-amber-100" role="status">
            {Math.ceil(left)} s left
          </p>
        )}
        {noSound && stage !== 'review' && (
          <p className="absolute inset-x-4 bottom-3 flex items-center justify-center gap-1.5 rounded-xl bg-black/70 p-2 text-center text-xs text-amber-100" role="status" data-testid="private-recorder-no-sound">
            <MicOff size={13} aria-hidden /> No sound: the microphone is not allowed for Jarvis.
          </p>
        )}
        {!ready && stage === 'ready' && !error && <Loader2 size={28} className="absolute animate-spin text-white/70" aria-label="Starting the camera" />}
        {error && (
          <p className="absolute inset-x-4 top-1/2 -translate-y-1/2 rounded-xl border border-amber-400/30 bg-amber-500/15 p-3 text-center text-sm text-amber-100" role="alert" data-testid="private-recorder-error">
            {error}
          </p>
        )}
      </div>
      {stage === 'review' && (
        <p className="shrink-0 px-4 pt-2 text-center text-xs text-slate-300" data-testid="private-recorder-length">
          {formatDuration(clip?.duration ?? 0)}
          {stoppedBy === 'time' ? ' (stopped at the 3 minute limit)' : stoppedBy === 'size' ? ' (stopped at the size limit)' : ''}
          {sending && sending.count > 0 ? ` · Sending ${sending.done} of ${sending.count}` : ''}
        </p>
      )}
      {onPhoto && stage === 'ready' && (
        <div className="flex shrink-0 justify-center gap-2 pt-3" role="group" aria-label="Camera mode">
          <button type="button" onClick={onPhoto} className="flex min-h-11 items-center gap-1.5 rounded-full px-4 text-sm font-semibold text-slate-300 hover:bg-white/10" data-testid="private-mode-photo">
            <Camera size={15} aria-hidden /> Photo
          </button>
          <button type="button" aria-pressed="true" className="flex min-h-11 items-center gap-1.5 rounded-full bg-white/15 px-4 text-sm font-semibold text-white" data-testid="private-mode-video">
            <Video size={15} aria-hidden /> Video
          </button>
        </div>
      )}
      <div className="flex shrink-0 items-center justify-around gap-3 px-4 pb-[max(1rem,var(--safe-bottom))] pt-3">
        {stage === 'review' ? (
          <>
            <button type="button" onClick={retake} disabled={Boolean(sending)} className="flex min-h-12 items-center gap-2 rounded-full bg-white/10 px-5 text-sm font-semibold text-white disabled:opacity-50" data-testid="private-recorder-retake">
              <RotateCcw size={16} aria-hidden /> Record again
            </button>
            <button type="button" onClick={() => void send()} disabled={Boolean(sending)} className="flex min-h-12 items-center gap-2 rounded-full bg-rose-600 px-5 text-sm font-semibold text-white disabled:opacity-50" data-testid="private-recorder-send">
              {sending ? <Loader2 size={16} className="animate-spin" /> : <SendHorizontal size={16} aria-hidden />} Send
            </button>
          </>
        ) : (
          <>
            <span className="h-12 w-12" aria-hidden />
            {recording ? (
              <button
                type="button"
                onClick={stop}
                aria-label="Stop recording"
                data-testid="private-recorder-stop"
                className="flex h-16 w-16 items-center justify-center rounded-full border-4 border-white bg-rose-600 text-white"
              >
                <Square size={22} className="fill-white" aria-hidden />
              </button>
            ) : (
              <button
                type="button"
                onClick={record}
                disabled={!ready}
                aria-label="Start recording"
                data-testid="private-recorder-record"
                className="flex h-16 w-16 items-center justify-center rounded-full border-4 border-white bg-white/20 disabled:opacity-40"
              >
                <span className="h-8 w-8 rounded-full bg-rose-600" aria-hidden />
              </button>
            )}
            <button
              type="button"
              onClick={() => setFacing((f) => (f === 'environment' ? 'user' : 'environment'))}
              disabled={!ready || recording}
              aria-label={facing === 'environment' ? 'Use the front camera' : 'Use the back camera'}
              data-testid="private-recorder-flip"
              className="flex h-12 w-12 items-center justify-center rounded-full bg-white/10 text-white disabled:opacity-40"
            >
              <RefreshCw size={18} aria-hidden />
            </button>
          </>
        )}
      </div>
    </div>
  );
}
