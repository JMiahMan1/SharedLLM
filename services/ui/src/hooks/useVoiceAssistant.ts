import { useState, useEffect, useCallback, useRef } from 'react';
import { Capacitor } from '@capacitor/core';
import { Haptics, ImpactStyle } from '@capacitor/haptics';

export interface VoiceState {
  isActive: boolean;
  isListening: boolean;
  isSpeaking: boolean;
  transcript: string;
  error: string | null;
}

export function useVoiceAssistant() {
  const [state, setState] = useState<VoiceState>({
    isActive: false,
    isListening: false,
    isSpeaking: false,
    transcript: '',
    error: null,
  });

  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animationFrameRef = useRef<number>(0);
  const onVolumeChangeRef = useRef<((level: number) => void) | null>(null);

  const startListening = useCallback(() => {
    if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
      setState((s) => ({ ...s, error: 'Speech recognition not supported' }));
      return;
    }

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';

    recognition.onstart = () => {
      setState((s) => ({ ...s, isListening: true, error: null }));
    };

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      let transcript = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      setState((s) => ({ ...s, transcript }));
    };

    recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
      setState((s) => ({ ...s, isListening: false, error: event.error }));
    };

    recognition.onend = () => {
      setState((s) => ({ ...s, isListening: false }));
    };

    recognitionRef.current = recognition;
    recognition.start();
  }, []);

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    setState((s) => ({ ...s, isListening: false }));
  }, []);

  const startAudioVisualization = useCallback(async (onVolumeChange: (level: number) => void) => {
    onVolumeChangeRef.current = onVolumeChange;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const audioContext = new AudioContext();
      const analyser = audioContext.createAnalyser();
      const source = audioContext.createMediaStreamSource(stream);

      analyser.fftSize = 256;
      source.connect(analyser);

      audioContextRef.current = audioContext;
      analyserRef.current = analyser;

      const dataArray = new Uint8Array(analyser.frequencyBinCount);

      const analyze = () => {
        analyser.getByteFrequencyData(dataArray);
        const average = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
        const level = average / 255;
        onVolumeChangeRef.current?.(level);
        animationFrameRef.current = requestAnimationFrame(analyze);
      };
      analyze();
    } catch {
      setState((s) => ({ ...s, error: 'Microphone access denied' }));
    }
  }, []);

  const stopAudioVisualization = useCallback(() => {
    cancelAnimationFrame(animationFrameRef.current);
    audioContextRef.current?.close();
    audioContextRef.current = null;
    analyserRef.current = null;
  }, []);

  const activate = useCallback(() => {
    setState({ isActive: true, isListening: true, isSpeaking: false, transcript: '', error: null });
    if (Capacitor.isNativePlatform()) {
      Haptics.impact({ style: ImpactStyle.Medium });
    }
    startListening();
    startAudioVisualization(() => {});
  }, [startListening, startAudioVisualization]);

  const deactivate = useCallback(() => {
    stopListening();
    stopAudioVisualization();
    setState({ isActive: false, isListening: false, isSpeaking: false, transcript: '', error: null });
  }, [stopListening, stopAudioVisualization]);

  useEffect(() => {
    return () => {
      stopListening();
      stopAudioVisualization();
    };
  }, [stopListening, stopAudioVisualization]);

  return {
    state,
    activate,
    deactivate,
    startAudioVisualization,
    stopAudioVisualization,
  };
}
