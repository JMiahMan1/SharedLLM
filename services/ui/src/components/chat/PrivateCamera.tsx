import { useCallback, useEffect, useRef, useState } from 'react';
import { Camera, Loader2, RefreshCw, RotateCcw, SendHorizontal, X } from 'lucide-react';

type Facing = 'environment' | 'user';

/**
 * The private chat's own camera. It never opens the phone's camera app and
 * never uses a file input with the capture attribute (either can save a copy to the phone's
 * gallery): the picture is taken from the camera stream in this page, drawn to
 * a canvas, turned into JPEG bytes in memory and handed to the chat, which
 * encrypts them before anything leaves the device. Nothing is written to disk.
 */
export default function PrivateCamera({
  onCapture,
  onClose,
}: {
  /** The JPEG bytes and when they were taken (seconds); the caller encrypts and zeroes them. */
  onCapture: (jpeg: Uint8Array<ArrayBuffer>, takenAt: number) => Promise<void>;
  onClose: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const takenAtRef = useRef(0);
  const [facing, setFacing] = useState<Facing>('environment');
  const [ready, setReady] = useState(false);
  const [captured, setCaptured] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');

  const stop = useCallback(() => {
    for (const track of streamRef.current?.getTracks() ?? []) track.stop();
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  }, []);

  /** Blank the still, so the picture does not stay in the page after it is sent or dropped. */
  const wipe = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height);
    canvas.width = 0;
    canvas.height = 0;
  }, []);

  useEffect(() => {
    if (captured) return;
    let live = true;
    const media = navigator.mediaDevices;
    if (!media?.getUserMedia) {
      window.setTimeout(() => live && setError('The camera is not available here.'), 0);
      return;
    }
    void media
      .getUserMedia({ video: { facingMode: { ideal: facing } }, audio: false })
      .then(async (stream) => {
        if (!live) {
          for (const track of stream.getTracks()) track.stop();
          return;
        }
        streamRef.current = stream;
        const video = videoRef.current;
        if (video) {
          video.srcObject = stream;
          await video.play().catch(() => {});
        }
        setError('');
        setReady(true);
      })
      .catch(() => {
        if (live) setError('Jarvis could not use the camera. Allow camera access for Jarvis and try again.');
      });
    return () => {
      live = false;
      setReady(false);
      stop();
    };
  }, [facing, captured, stop]);

  useEffect(() => () => wipe(), [wipe]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const take = () => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !video.videoWidth) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d')?.drawImage(video, 0, 0, canvas.width, canvas.height);
    takenAtRef.current = Math.floor(Date.now() / 1000);
    stop();
    setCaptured(true);
  };

  const retake = () => {
    wipe();
    setCaptured(false);
  };

  const send = async () => {
    const canvas = canvasRef.current;
    if (!canvas || sending) return;
    setSending(true);
    setError('');
    try {
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9));
      if (!blob) throw new Error('Could not take the picture.');
      await onCapture(new Uint8Array(await blob.arrayBuffer()), takenAtRef.current || Math.floor(Date.now() / 1000));
      wipe();
      onClose();
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : 'Could not send the picture.');
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[90] flex flex-col bg-black" role="dialog" aria-modal="true" aria-label="Camera" data-testid="private-camera">
      <div className="flex shrink-0 items-center gap-2 px-3 pt-[max(0.75rem,var(--safe-top))]">
        <button type="button" onClick={onClose} aria-label="Close camera" className="flex h-11 w-11 items-center justify-center rounded-full bg-white/10 text-white">
          <X size={20} />
        </button>
        <p className="min-w-0 flex-1 truncate text-center text-sm text-slate-200">Encrypted, never saved to the phone</p>
        <span className="h-11 w-11 shrink-0" aria-hidden />
      </div>
      <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden">
        <video
          ref={videoRef}
          playsInline
          muted
          className={`max-h-full max-w-full object-contain ${captured ? 'hidden' : ''} ${facing === 'user' ? '-scale-x-100' : ''}`}
          data-testid="private-camera-preview"
        />
        <canvas ref={canvasRef} className={`max-h-full max-w-full object-contain ${captured ? '' : 'hidden'}`} data-testid="private-camera-still" />
        {!ready && !captured && !error && <Loader2 size={28} className="absolute animate-spin text-white/70" aria-label="Starting the camera" />}
        {error && (
          <p className="absolute inset-x-4 top-1/2 -translate-y-1/2 rounded-xl border border-amber-400/30 bg-amber-500/15 p-3 text-center text-sm text-amber-100" role="alert" data-testid="private-camera-error">
            {error}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center justify-around gap-3 px-4 pb-[max(1rem,var(--safe-bottom))] pt-3">
        {captured ? (
          <>
            <button type="button" onClick={retake} disabled={sending} className="flex min-h-12 items-center gap-2 rounded-full bg-white/10 px-5 text-sm font-semibold text-white disabled:opacity-50" data-testid="private-camera-retake">
              <RotateCcw size={16} aria-hidden /> Retake
            </button>
            <button type="button" onClick={() => void send()} disabled={sending} className="flex min-h-12 items-center gap-2 rounded-full bg-rose-600 px-5 text-sm font-semibold text-white disabled:opacity-50" data-testid="private-camera-send">
              {sending ? <Loader2 size={16} className="animate-spin" /> : <SendHorizontal size={16} aria-hidden />} Send
            </button>
          </>
        ) : (
          <>
            <span className="h-12 w-12" aria-hidden />
            <button
              type="button"
              onClick={take}
              disabled={!ready}
              aria-label="Take the picture"
              data-testid="private-camera-shutter"
              className="flex h-16 w-16 items-center justify-center rounded-full border-4 border-white bg-white/20 text-white disabled:opacity-40"
            >
              <Camera size={24} aria-hidden />
            </button>
            <button
              type="button"
              onClick={() => setFacing((f) => (f === 'environment' ? 'user' : 'environment'))}
              disabled={!ready}
              aria-label={facing === 'environment' ? 'Use the front camera' : 'Use the back camera'}
              data-testid="private-camera-flip"
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
