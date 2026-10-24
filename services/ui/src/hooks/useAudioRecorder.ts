import { useRef, useState } from 'react';

export type RecorderStartResult = 'started' | 'unsupported' | 'denied';

/**
 * Record one audio clip with the microphone (MediaRecorder).
 *
 * Shared by the chat composer's voice notes and the Health hub's voice food
 * logging. `onClip` gets the finished, non-empty clip when recording stops;
 * the microphone is released as soon as it does.
 */
export function useAudioRecorder(onClip: (blob: Blob) => void | Promise<void>) {
  const [recording, setRecording] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const start = async (): Promise<RecorderStartResult> => {
    if (!navigator.mediaDevices?.getUserMedia) return 'unsupported';
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        if (blob.size > 0) void onClip(blob);
      };
      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
      return 'started';
    } catch {
      return 'denied';
    }
  };

  const stop = () => {
    recorderRef.current?.stop();
    recorderRef.current = null;
    setRecording(false);
  };

  return { recording, start, stop };
}
