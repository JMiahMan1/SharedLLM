import { useEffect, useRef, useState } from 'react';

/** Formats tried in order; Opus in WebM is the smallest where it is offered. */
const PREFERRED_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];

function preferredType(): string | undefined {
  if (typeof MediaRecorder.isTypeSupported !== 'function') return undefined;
  return PREFERRED_TYPES.find((type) => MediaRecorder.isTypeSupported(type));
}

/**
 * Record from the microphone (chat voice messages, note voice notes, voice
 * food logging, voice signatures).
 *
 * `start` asks for the microphone and begins; `stop` ends the take and hands
 * the recording to `onRecorded` as one Blob (Opus WebM where offered, else
 * the recorder's own format: `audio/mp4` on Safari). `start(maxSeconds)` also
 * stops the take by itself after that long, counting down in `secondsLeft`.
 * An empty take is reported through `onError` ('empty'), so a caller waiting
 * on `onRecorded`/`onError` always settles. `onError` says why nothing was
 * recorded: no recorder here, the microphone refused, or the take came back
 * empty. A take still running when the component goes away is stopped and its
 * microphone released.
 */
export function useAudioRecorder({
  onRecorded,
  onError,
}: {
  onRecorded: (blob: Blob) => void;
  onError: (reason: 'unavailable' | 'denied' | 'empty') => void;
}) {
  const [recording, setRecording] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const handlers = useRef({ onRecorded, onError });
  useEffect(() => {
    handlers.current = { onRecorded, onError };
  });

  const clearTimer = () => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
  };

  const stop = () => {
    clearTimer();
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
    recorderRef.current = null;
    setRecording(false);
    setSecondsLeft(0);
  };

  useEffect(
    () => () => {
      clearTimer();
      if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
    },
    [],
  );

  const start = async (maxSeconds?: number) => {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      handlers.current.onError('unavailable');
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      handlers.current.onError('denied');
      return;
    }
    let recorder: MediaRecorder;
    try {
      const mimeType = preferredType();
      recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    } catch {
      stream.getTracks().forEach((track) => track.stop());
      handlers.current.onError('unavailable');
      return;
    }
    chunksRef.current = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    };
    recorder.onstop = () => {
      stream.getTracks().forEach((track) => track.stop());
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || chunksRef.current[0]?.type || 'audio/webm' });
      // Always settle: an empty take must not leave the caller waiting.
      if (blob.size > 0) handlers.current.onRecorded(blob);
      else handlers.current.onError('empty');
    };
    recorderRef.current = recorder;
    recorder.start();
    setRecording(true);
    if (maxSeconds && maxSeconds > 0) {
      setSecondsLeft(maxSeconds);
      timerRef.current = setInterval(() => {
        setSecondsLeft((left) => {
          if (left <= 1) {
            // Stop outside the state updater, which must stay pure.
            queueMicrotask(stop);
            return 0;
          }
          return left - 1;
        });
      }, 1000);
    }
  };

  return { recording, secondsLeft, start, stop };
}
