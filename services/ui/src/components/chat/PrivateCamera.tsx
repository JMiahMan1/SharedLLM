import { useCallback, useEffect, useRef, useState } from 'react';
import { Camera, Loader2, RefreshCw, RotateCcw, SendHorizontal, SlidersHorizontal, Video, X } from 'lucide-react';
import LazyPhotoEditor from '../photoEditor/LazyPhotoEditor';
import type { EditRecipe } from '../../lib/photoEdit/recipe';
import { MAX_ATTACHMENT_BYTES } from './chatModel';
import { GallerySaveToggle } from './GallerySaveToggle';

type Facing = 'environment' | 'user';

/** What an edited picture was made from: the recipe and the untouched original, for whoever keeps them. */
export interface CapturedEdit {
  recipe: EditRecipe;
  original: Blob;
}

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
  onVideo,
}: {
  /**
   * The JPEG bytes and when they were taken (seconds); the caller encrypts and zeroes them.
   * After an edit, `edit` carries the recipe and the original picture (still in memory only).
   */
  /** `saveToGallery` is the preview's switch, off unless the person turned it on. */
  onCapture: (jpeg: Uint8Array<ArrayBuffer>, takenAt: number, edit?: CapturedEdit, saveToGallery?: boolean) => Promise<void>;
  onClose: () => void;
  /** Switch to recording a video clip (PrivateVideoRecorder), when the chat offers it. */
  onVideo?: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const takenAtRef = useRef(0);
  const [facing, setFacing] = useState<Facing>('environment');
  const [ready, setReady] = useState(false);
  const [captured, setCaptured] = useState(false);
  const [sending, setSending] = useState(false);
  const [toGallery, setToGallery] = useState(false);
  const [error, setError] = useState('');
  /** The taken picture, while it is open in the editor. */
  const [editing, setEditing] = useState<Blob | null>(null);

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
      // The editor answers Escape itself (back to this screen).
      if (event.key === 'Escape' && !editing) onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose, editing]);

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
    // A retake is a new picture: the last one's "Save to gallery" tick was
    // about that picture, and keeping it would quietly file the next shot.
    // PrivateVideoRecorder and PrivateAudioRecorder reset theirs the same way.
    setToGallery(false);
  };

  const still = async (): Promise<Blob> => {
    const canvas = canvasRef.current;
    const blob = canvas ? await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9)) : null;
    if (!blob) throw new Error('Could not take the picture.');
    return blob;
  };

  const takenAt = () => takenAtRef.current || Math.floor(Date.now() / 1000);

  const send = async () => {
    if (!canvasRef.current || sending) return;
    setSending(true);
    setError('');
    try {
      const blob = await still();
      await onCapture(new Uint8Array(await blob.arrayBuffer()), takenAt(), undefined, toGallery);
      wipe();
      onClose();
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : 'Could not send the picture.');
    } finally {
      setSending(false);
    }
  };

  /** Open the taken picture in the editor; the still stays here, so closing the editor comes back to it. */
  const edit = async () => {
    if (sending) return;
    setError('');
    try {
      setEditing(await still());
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : 'Could not open the editor.');
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
      {onVideo && !captured && (
        <div className="flex shrink-0 justify-center gap-2 pt-3" role="group" aria-label="Camera mode">
          <button type="button" aria-pressed="true" className="flex min-h-11 items-center gap-1.5 rounded-full bg-white/15 px-4 text-sm font-semibold text-white" data-testid="private-mode-photo">
            <Camera size={15} aria-hidden /> Photo
          </button>
          <button type="button" onClick={onVideo} className="flex min-h-11 items-center gap-1.5 rounded-full px-4 text-sm font-semibold text-slate-300 hover:bg-white/10" data-testid="private-mode-video">
            <Video size={15} aria-hidden /> Video
          </button>
        </div>
      )}
      <div className={`flex shrink-0 items-center gap-2 px-3 pb-[max(1rem,var(--safe-bottom))] pt-3 ${captured ? 'flex-wrap justify-center' : 'justify-around'}`}>
        {captured ? (
          <>
            <div className="w-full">
              <GallerySaveToggle
                checked={toGallery}
                onChange={setToGallery}
                what="this picture"
                testId="private-camera-gallery"
                disabled={sending}
              />
            </div>
            <button type="button" onClick={retake} disabled={sending} className="flex min-h-12 items-center gap-1.5 rounded-full bg-white/10 px-4 text-sm font-semibold text-white disabled:opacity-50" data-testid="private-camera-retake">
              <RotateCcw size={16} aria-hidden /> Retake
            </button>
            <button type="button" onClick={() => void edit()} disabled={sending} className="flex min-h-12 items-center gap-1.5 rounded-full bg-white/10 px-4 text-sm font-semibold text-white disabled:opacity-50" data-testid="private-camera-edit">
              <SlidersHorizontal size={16} aria-hidden /> Edit
            </button>
            <button type="button" onClick={() => void send()} disabled={sending} className="flex min-h-12 items-center gap-1.5 rounded-full bg-rose-600 px-4 text-sm font-semibold text-white disabled:opacity-50" data-testid="private-camera-send">
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
      {editing && (
        <LazyPhotoEditor
          source={editing}
          maxBytes={MAX_ATTACHMENT_BYTES}
          doneLabel="Send"
          onCancel={() => setEditing(null)}
          onAsIs={async () => {
            await onCapture(new Uint8Array(await editing.arrayBuffer()), takenAt(), undefined, toGallery);
            setEditing(null);
            wipe();
            onClose();
          }}
          onDone={async (result) => {
            // A still from the camera comes back as a JPEG; anything else would not be what the chat expects.
            if (result.mime !== 'image/jpeg') throw new Error('Could not make the edited picture.');
            await onCapture(new Uint8Array(await result.blob.arrayBuffer()), takenAt(), { recipe: result.recipe, original: result.original }, toGallery);
            setEditing(null);
            wipe();
            onClose();
          }}
        />
      )}
    </div>
  );
}
