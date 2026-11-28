import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Mic, SendHorizontal, Square, Trash2, X } from 'lucide-react';
import { audioBaseType, isAudioMime } from '../../lib/privateChatCrypto';
import { useAudioRecorder } from '../../hooks/useAudioRecorder';
import { audioLength } from './privateAudioTime';
import { errorText } from './privateChatModel';
import { GallerySaveToggle } from './GallerySaveToggle';

/** The longest one take may run: five minutes is a long voice note and a small sealed blob. */
export const MAX_AUDIO_TAKE_S = 300;

/** "0:07" while recording, from the second the microphone opened. */
function elapsedLabel(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

/**
 * Record a voice note in Private Chat. The take lives in memory only: nothing
 * is written to disk, nothing is uploaded until Send is pressed, and the
 * object URL used for the preview is revoked on discard, on send, on close and
 * on unmount.
 *
 * A take that comes back empty, or a microphone that is refused, says so in
 * place rather than leaving a recorder that looks like it is working.
 */
export default function PrivateAudioRecorder({
  onClose,
  onSend,
}: {
  onClose: () => void;
  /** Hand the take over: the caller encrypts it, posts it and closes this sheet.
   *  `saveToGallery` is the switch on the preview, off unless it was turned on. */
  onSend: (blob: Blob, mime: string, duration: number, caption: string, saveToGallery: boolean) => Promise<void>;
}) {
  const [take, setTake] = useState<{ blob: Blob; url: string; mime: string; duration: number } | null>(null);
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [caption, setCaption] = useState('');
  const [saving, setSaving] = useState(false);
  const [toGallery, setToGallery] = useState(false);
  const [problem, setProblem] = useState('');
  const startedAt = useRef(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const takeUrl = useRef<string>('');

  const releaseTake = useCallback(() => {
    if (takeUrl.current) URL.revokeObjectURL(takeUrl.current);
    takeUrl.current = '';
  }, []);

  const clearTimer = useCallback(() => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
  }, []);

  const recorder = useAudioRecorder({
    onRecorded: (blob) => {
      clearTimer();
      setRecording(false);
      const duration = startedAt.current ? Math.max(1, Math.round((Date.now() - startedAt.current) / 1000)) : 0;
      releaseTake();
      const url = URL.createObjectURL(blob);
      takeUrl.current = url;
      const mime = isAudioMime(blob.type) ? audioBaseType(blob.type) : 'audio/webm';
      setProblem('');
      setTake({ blob, url, mime, duration });
    },
    onError: (reason) => {
      clearTimer();
      setRecording(false);
      setProblem(
        reason === 'denied'
          ? 'The microphone was not allowed. Allow it for this app and try again.'
          : reason === 'empty'
            ? 'That take was empty. Try again and say something.'
            : 'This device cannot record audio here.',
      );
    },
  });

  // Closing the sheet, or leaving the chat, releases the take and the microphone.
  useEffect(
    () => () => {
      clearTimer();
      releaseTake();
    },
    [clearTimer, releaseTake],
  );

  const begin = async () => {
    setProblem('');
    setSeconds(0);
    startedAt.current = Date.now();
    await recorder.start(MAX_AUDIO_TAKE_S);
    setRecording(true);
    clearTimer();
    timer.current = setInterval(() => {
      const now = Math.max(0, Math.round((Date.now() - startedAt.current) / 1000));
      setSeconds(now);
      if (now >= MAX_AUDIO_TAKE_S) recorder.stop();
    }, 500);
  };

  /** Cancel: stop the take (nothing is kept) and say nothing was recorded. */
  const cancelTake = () => {
    clearTimer();
    recorder.stop();
    setRecording(false);
    setSeconds(0);
    startedAt.current = 0;
  };

  const discard = () => {
    releaseTake();
    setTake(null);
    setCaption('');
    setToGallery(false);
    setSeconds(0);
    startedAt.current = 0;
  };

  const send = async () => {
    if (!take || saving) return;
    setSaving(true);
    try {
      await onSend(take.blob, take.mime, take.duration, caption.trim(), toGallery);
      discard();
      onClose();
    } catch (error) {
      // The take is kept, so a failed send can be tried again without re-recording.
      setProblem(errorText(error, 'Could not send the voice note'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[95] flex items-end justify-center bg-black/85 p-3 sm:items-center" role="dialog" aria-modal="true" aria-label="Record a voice note">
      <div className="glass-card w-full max-w-sm space-y-4 rounded-2xl p-4">
        <div className="flex items-center justify-between">
          <p className="flex items-center gap-1.5 text-sm font-semibold text-white">
            <Mic size={15} aria-hidden /> Voice note
          </p>
          <button
            type="button"
            onClick={() => {
              cancelTake();
              onClose();
            }}
            aria-label="Close the voice recorder"
            className="flex h-9 w-9 items-center justify-center rounded-full bg-white/10 text-slate-200"
          >
            <X size={17} />
          </button>
        </div>

        {!take && (
          <div className="space-y-3 text-center">
            <p className={`text-3xl font-semibold tabular-nums ${recording ? 'text-rose-300' : 'text-slate-300'}`} data-testid="private-audio-clock">
              {elapsedLabel(seconds)}
            </p>
            <p className="text-xs text-slate-400">
              {recording ? 'Recording. Stop when you are done, or cancel to keep nothing.' : `Up to ${audioLength(MAX_AUDIO_TAKE_S)}. Nothing leaves this phone until you send it.`}
            </p>
            <div className="flex items-center justify-center gap-2">
              {!recording ? (
                <button
                  type="button"
                  onClick={() => void begin()}
                  data-testid="private-audio-start"
                  className="flex min-h-11 items-center gap-2 rounded-full bg-rose-600 px-5 text-sm font-semibold text-white"
                >
                  <Mic size={16} aria-hidden /> Record
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={() => {
                      recorder.stop();
                    }}
                    data-testid="private-audio-stop"
                    className="flex min-h-11 items-center gap-2 rounded-full bg-rose-600 px-5 text-sm font-semibold text-white"
                  >
                    <Square size={14} aria-hidden /> Stop
                  </button>
                  <button
                    type="button"
                    onClick={cancelTake}
                    data-testid="private-audio-cancel"
                    className="flex min-h-11 items-center gap-2 rounded-full border border-white/15 bg-white/5 px-4 text-sm font-semibold text-slate-200"
                  >
                    <Trash2 size={14} aria-hidden /> Cancel
                  </button>
                </>
              )}
            </div>
          </div>
        )}

        {take && (
          <div className="space-y-3">
            <audio
              controls
              controlsList="nodownload noplaybackrate"
              preload="metadata"
              src={take.url}
              aria-label="Preview the voice note"
              data-testid="private-audio-preview"
              className="h-9 w-full"
            />
            <p className="text-[11px] text-slate-400">{audioLength(take.duration)} recorded. Listen, then send it or record again.</p>
            <label className="block text-[11px] font-semibold text-slate-300">
              A note to go with it (optional)
              <textarea
                value={caption}
                onChange={(event) => setCaption(event.target.value)}
                rows={2}
                maxLength={500}
                data-testid="private-audio-caption"
                className="glass-input mt-1 min-h-11 w-full resize-none text-base sm:text-sm"
              />
            </label>
            <GallerySaveToggle
              checked={toGallery}
              onChange={setToGallery}
              what="this voice note"
              testId="private-audio-gallery"
              disabled={saving}
            />
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => void send()}
                disabled={saving}
                data-testid="private-audio-send"
                className="flex min-h-11 flex-1 items-center justify-center gap-2 rounded-full bg-rose-600 px-4 text-sm font-semibold text-white disabled:opacity-50"
              >
                {saving ? <Loader2 size={16} className="animate-spin" /> : <SendHorizontal size={16} aria-hidden />} Send
              </button>
              <button
                type="button"
                onClick={discard}
                disabled={saving}
                data-testid="private-audio-discard"
                className="flex min-h-11 items-center gap-2 rounded-full border border-white/15 bg-white/5 px-4 text-sm font-semibold text-slate-200 disabled:opacity-50"
              >
                <Trash2 size={14} aria-hidden /> Discard
              </button>
            </div>
          </div>
        )}

        {problem && (
          <p className="rounded-lg border border-amber-400/30 bg-amber-400/10 px-2.5 py-2 text-[11px] text-amber-200" data-testid="private-audio-problem" role="status">
            {problem}
          </p>
        )}
      </div>
    </div>
  );
}
