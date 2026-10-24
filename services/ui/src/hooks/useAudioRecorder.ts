import { useEffect, useRef, useState } from 'react';

/**
 * Record from the microphone (chat voice messages, note voice notes).
 *
 * `start` asks for the microphone and begins; `stop` ends the take and hands
 * the recording to `onRecorded` as one Blob in the recorder's own format
 * (`audio/webm` on Chrome and the Android webview, `audio/mp4` on Safari).
 * An empty take is dropped. `onError` says why nothing was recorded: no
 * recorder here, or the microphone refused. A take still running when the
 * component goes away is stopped and its microphone released.
 */
export function useAudioRecorder({
  onRecorded,
  onError,
}: {
  onRecorded: (blob: Blob) => void;
  onError: (reason: 'unavailable' | 'denied') => void;
}) {
  const [recording, setRecording] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const handlers = useRef({ onRecorded, onError });
  useEffect(() => {
    handlers.current = { onRecorded, onError };
  });
  useEffect(
    () => () => {
      if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
    },
    [],
  );

  const start = async () => {
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
      recorder = new MediaRecorder(stream);
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
      if (blob.size > 0) handlers.current.onRecorded(blob);
    };
    recorderRef.current = recorder;
    recorder.start();
    setRecording(true);
  };

  const stop = () => {
    recorderRef.current?.stop();
    recorderRef.current = null;
    setRecording(false);
  };

  return { recording, start, stop };
}
