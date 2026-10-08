import { useEffect, useRef, useState } from 'react';
import { BarChart3, Loader2, Mic, Plus, Send, Square, X } from 'lucide-react';
import toast from 'react-hot-toast';
import type { TalkMessage } from './chatModel';
import SendAsSelector from './SendAsSelector';
import type { SendAs } from './sendAsPref';

interface VoicePayload {
  audio_base64: string;
  mime_type: string;
  caption?: string;
}

interface ComposerProps {
  disabled: boolean;
  replyTo: TalkMessage | null;
  onCancelReply: () => void;
  onSend: (text: string) => void;
  onSendVoice: (payload: VoicePayload) => Promise<boolean>;
  sendingVoice: boolean;
  onNewPoll: () => void;
  isAdmin: boolean;
  sendAs: SendAs;
  onSendAsChange: (next: SendAs) => void;
}

const MAX_INPUT_HEIGHT_PX = 140;

export default function Composer({
  disabled,
  replyTo,
  onCancelReply,
  onSend,
  onSendVoice,
  sendingVoice,
  onNewPoll,
  isAdmin,
  sendAs,
  onSendAsChange,
}: ComposerProps) {
  const [draft, setDraft] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const [recording, setRecording] = useState(false);
  const [clip, setClip] = useState<{ url: string; base64: string; mimeType: string } | null>(null);
  const [caption, setCaption] = useState('');
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  // Grow with the text, up to a few lines, like every mobile messenger.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_INPUT_HEIGHT_PX)}px`;
  }, [draft]);

  useEffect(() => {
    if (replyTo) inputRef.current?.focus();
  }, [replyTo]);

  const submit = () => {
    const text = draft.trim();
    if (!text || disabled) return;
    onSend(text);
    setDraft('');
  };

  const startRecording = async () => {
    if (disabled) {
      toast.error('Pick a conversation first');
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      toast.error('Recording is not available on this device');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        if (blob.size === 0) return;
        const bytes = new Uint8Array(await blob.arrayBuffer());
        let binary = '';
        for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
        // Preview before sending: recording is easy to do by accident, and a
        // caption often carries the point of a voice note.
        setClip({ url: URL.createObjectURL(blob), base64: btoa(binary), mimeType: blob.type });
      };
      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch {
      toast.error('Microphone permission is needed to record a voice message');
    }
  };

  const stopRecording = () => {
    recorderRef.current?.stop();
    recorderRef.current = null;
    setRecording(false);
  };

  const discardClip = () => {
    if (clip) URL.revokeObjectURL(clip.url);
    setClip(null);
    setCaption('');
  };

  const sendClip = async () => {
    if (!clip) return;
    const sent = await onSendVoice({ audio_base64: clip.base64, mime_type: clip.mimeType, caption: caption.trim() });
    if (sent) discardClip();
  };

  const hasText = draft.trim().length > 0;
  const adminMode = isAdmin && sendAs === 'admin';

  return (
    <div className="shrink-0 border-t border-white/5 bg-slate-950/80 px-2 pt-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] backdrop-blur sm:px-3">
      {replyTo && (
        <div className="mb-2 flex items-center gap-2 rounded-xl border-l-2 border-purple-400 bg-white/5 px-3 py-1.5" data-testid="reply-preview">
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-semibold text-purple-200">Replying to {replyTo.actor_display_name || 'message'}</p>
            <p className="truncate text-xs text-slate-400">{replyTo.message}</p>
          </div>
          <button type="button" aria-label="Cancel reply" className="p-1 text-slate-400 hover:text-slate-100" onClick={onCancelReply}>
            <X size={14} />
          </button>
        </div>
      )}

      {clip && (
        <div className="mb-2 space-y-2 rounded-2xl border border-white/10 bg-white/5 p-2" data-testid="voice-preview">
          <p className="text-[11px] text-slate-300">Recorded clip ready</p>
          <audio controls src={clip.url} className="h-9 w-full" />
          <input
            value={caption}
            onChange={(event) => setCaption(event.target.value)}
            placeholder="Add a caption (optional)"
            aria-label="Voice message caption"
            className="glass-input w-full text-base sm:text-sm"
          />
          <div className="flex gap-2">
            <button type="button" className="glass-button flex-1 min-h-11 px-3 py-2 text-sm" disabled={sendingVoice} onClick={() => void sendClip()}>
              {sendingVoice ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />} Send voice
            </button>
            <button type="button" className="glass-button px-3 py-2 text-sm" onClick={discardClip}>
              Discard
            </button>
          </div>
        </div>
      )}

      {isAdmin && (
        <div className="mb-1.5 flex items-center gap-2 px-1">
          <SendAsSelector value={sendAs} onChange={onSendAsChange} />
          {adminMode && <span className="text-[10px] text-slate-500">in this chat only</span>}
        </div>
      )}

      <div className="relative flex items-end gap-1.5">
        <div className="relative">
          <button
            type="button"
            className="flex min-h-11 min-w-11 items-center justify-center rounded-full text-slate-300 hover:bg-white/10"
            aria-label="More options"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((v) => !v)}
          >
            <Plus size={20} className={`transition ${menuOpen ? 'rotate-45' : ''}`} />
          </button>
          {menuOpen && (
            <div className="absolute bottom-12 left-0 z-10 w-44 rounded-2xl border border-white/10 bg-slate-900 p-1 shadow-xl" role="menu">
              <button
                type="button"
                role="menuitem"
                className="flex w-full items-center gap-2 rounded-xl px-3 py-2.5 text-left text-sm text-slate-100 hover:bg-white/10"
                onClick={() => {
                  setMenuOpen(false);
                  onNewPoll();
                }}
              >
                <BarChart3 size={15} className="text-fuchsia-300" /> New poll
              </button>
            </div>
          )}
        </div>

        <textarea
          ref={inputRef}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              submit();
            }
            if (event.key === 'Escape' && replyTo) onCancelReply();
          }}
          rows={1}
          placeholder={adminMode ? 'Message as Admin' : 'Message'}
          aria-label="Message"
          className={`min-h-11 flex-1 resize-none rounded-3xl border bg-white/5 px-4 py-2.5 text-base leading-snug text-slate-100 placeholder:text-slate-500 focus:outline-none sm:text-[15px] ${
            adminMode ? 'border-amber-400/40 focus:border-amber-400/70' : 'border-white/10 focus:border-purple-400/50'
          }`}
        />

        {hasText ? (
          <button
            type="button"
            className="flex min-h-11 min-w-11 items-center justify-center rounded-full bg-purple-600 text-white transition hover:bg-purple-500 disabled:opacity-50"
            onClick={submit}
            disabled={disabled}
            aria-label="Send message"
          >
            <Send size={18} />
          </button>
        ) : (
          <button
            type="button"
            className={`flex min-h-11 min-w-11 items-center justify-center rounded-full transition ${
              recording ? 'animate-pulse bg-rose-500 text-white' : 'text-slate-300 hover:bg-white/10'
            }`}
            onClick={() => (recording ? stopRecording() : void startRecording())}
            disabled={sendingVoice}
            aria-label={recording ? 'Stop recording' : 'Record a voice message'}
            aria-pressed={recording}
          >
            {recording ? <Square size={16} /> : <Mic size={20} />}
          </button>
        )}
      </div>
    </div>
  );
}
