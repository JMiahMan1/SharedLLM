import { useState, useEffect, useCallback } from 'react';
import { useVoiceAssistant } from '../../hooks/useVoiceAssistant';
import { X, Mic, Loader2 } from 'lucide-react';

interface VoiceAssistantOverlayProps {
  isOpen: boolean;
  onClose: () => void;
  onCommand?: (transcript: string) => void;
}

const VoiceAssistantOverlay = ({ isOpen, onClose, onCommand }: VoiceAssistantOverlayProps) => {
  const { state, activate, deactivate, startAudioVisualization, stopAudioVisualization } = useVoiceAssistant();
  const [bars, setBars] = useState<number[]>(Array.from({ length: 32 }, () => 0));

  useEffect(() => {
    if (state.isActive) {
      startAudioVisualization(() => {
        setBars(() => Array.from({ length: 32 }, () => Math.random() * 100));
      });
    } else {
      stopAudioVisualization();
      setBars(Array.from({ length: 32 }, () => 0));
    }
  }, [state.isActive, startAudioVisualization, stopAudioVisualization]);

  useEffect(() => {
    if (isOpen) {
      activate();
    } else {
      deactivate();
    }
  }, [isOpen, activate, deactivate]);

  const handleSubmit = useCallback(() => {
    if (state.transcript.trim()) {
      onCommand?.(state.transcript);
      onClose();
    }
  }, [state.transcript, onCommand, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-3xl">
      <div className="flex flex-col items-center gap-6 w-full max-w-md px-6">
        <button
          onClick={onClose}
          className="absolute top-6 right-6 p-2 rounded-full bg-white/10 hover:bg-white/20 transition-colors"
        >
          <X size={20} className="text-white" />
        </button>

        <div className="flex flex-col items-center gap-2">
          <div className={`w-20 h-20 rounded-full flex items-center justify-center transition-all duration-300 ${
            state.isListening
              ? 'bg-purple-500/30 border-2 border-purple-400/50 shadow-lg shadow-purple-500/20'
              : state.isSpeaking
              ? 'bg-cyan-500/30 border-2 border-cyan-400/50 shadow-lg shadow-cyan-500/20'
              : 'bg-white/10 border-2 border-white/20'
          }`}>
          {state.isListening ? (
            <Mic size={32} className="text-purple-400 animate-pulse" />
          ) : state.isSpeaking ? (
            <Loader2 size={32} className="text-cyan-400 animate-spin" />
          ) : (
            <Mic size={32} className="text-slate-400" />
          )}
        </div>
        <p className="text-sm text-slate-400">
          {state.isListening ? 'Listening...' : state.isSpeaking ? 'Processing...' : 'Tap to speak'}
        </p>
      </div>

      <div className="w-full h-24 flex items-center justify-center gap-1">
        {bars.map((height, i) => (
          <div
            key={i}
            className="w-1 rounded-full bg-gradient-to-t from-purple-500 to-cyan-400 transition-all duration-75"
            style={{
              height: `${Math.max(4, height)}%`,
              opacity: 0.4 + (height / 100) * 0.6,
            }}
          />
        ))}
      </div>

      {state.transcript && (
        <div className="w-full glass-panel p-4 min-h-[60px]">
          <p className="text-sm text-slate-300 text-center">{state.transcript}</p>
        </div>
      )}

      {state.error && (
        <p className="text-sm text-red-400 text-center">{state.error}</p>
      )}

      <div className="flex gap-4 w-full">
        <button
          onClick={onClose}
          className="flex-1 py-3 rounded-xl bg-white/10 border border-white/20 text-white font-medium hover:bg-white/20 transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={handleSubmit}
          disabled={!state.transcript.trim()}
          className="flex-1 py-3 rounded-xl bg-purple-500/30 border border-purple-500/30 text-white font-medium hover:bg-purple-500/40 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
        >
          Send
        </button>
      </div>
    </div>
  </div>
);
};

export default VoiceAssistantOverlay;
