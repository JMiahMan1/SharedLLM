import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Loader2, Mic, Send, Square, X } from 'lucide-react';
import { api } from '../../services/api';
import { Capacitor } from '@capacitor/core';
import { JarvisAssist, type AssistState } from '../../plugins/jarvisAssist';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onCommand?: (transcript: string) => void;
  userId?: string;
}

/** The gateway answers a typed turn in one of these shapes; read the words out of whichever arrived. */
function replyText(data: unknown): string {
  if (!data || typeof data !== 'object') return typeof data === 'string' ? data : '';
  const d = data as Record<string, unknown>;
  const message = d.message as { content?: unknown } | undefined;
  if (typeof d.reply === 'string') return d.reply;
  if (typeof message?.content === 'string') return message.content;
  const nested = d.data as Record<string, unknown> | undefined;
  if (typeof nested?.reply === 'string') return nested.reply;
  if (typeof d.response === 'string') return d.response;
  return '';
}

const STATE_WORDS: Record<AssistState, string> = {
  idle: 'Tap to talk',
  listening: 'Listening',
  thinking: 'Thinking',
  speaking: 'Speaking',
};

/**
 * The phone's voice assistant: press once, talk, hear the answer spoken back
 * while it is still being written. Unlike a cloud assistant it never goes
 * quiet — if the answer is slow it says so, and if it cannot hear you it says
 * that too. Typing is always available, because a phone in a quiet room is not
 * always a phone you can talk to.
 */
export default function VoiceAssistantOverlay({ isOpen, onClose, onCommand, userId }: Props) {
  const [state, setState] = useState<AssistState>('idle');
  const [level, setLevel] = useState(0);
  const [heard, setHeard] = useState('');
  const [said, setSaid] = useState('');
  const [error, setError] = useState('');
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const native = Capacitor.isNativePlatform();
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Every event the service emits lands here: state, mic level, what it heard,
  // each sentence as it is spoken, and the whole reply at the end.
  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    let handle: { remove: () => Promise<void> } | undefined;
    void JarvisAssist.addListener('assist', (event) => {
      if (cancelled) return;
      if (event.type === 'state' && event.text) setState(event.text as AssistState);
      else if (event.type === 'level') setLevel(Math.max(0, Math.min(100, event.level || 0)));
      else if (event.type === 'transcript') {
        setHeard(event.text || '');
        setSaid('');
        if (event.text) onCommand?.(event.text);
      } else if (event.type === 'sentence') setSaid((prev) => (prev ? `${prev} ${event.text || ''}` : event.text || ''));
      else if (event.type === 'reply') setSaid(event.text || '');
      else if (event.type === 'error') setError(event.text || 'That did not work.');
    }).then((h) => {
      if (cancelled) void h.remove();
      else handle = h;
    });
    return () => {
      cancelled = true;
      void handle?.remove();
    };
  }, [isOpen, onCommand]);

  // Opening the sheet starts a turn: pressing the microphone should never make
  // you press something else first.
  useEffect(() => {
    if (!isOpen) return;
    /* eslint-disable react-hooks/set-state-in-effect -- opening the sheet is a
       deliberate reset of the turn before it; it runs once per open, never in a
       render loop. */
    setError('');
    setHeard('');
    setSaid('');
    setLevel(0);
    if (!native) {
      setTyping(true);
      return;
    }
    setState('listening');
    /* eslint-enable react-hooks/set-state-in-effect */
    JarvisAssist.start({ wake: false }).catch((e: Error) => {
      setState('idle');
      setTyping(true);
      setError(e?.message || 'Jarvis needs the microphone to hear you.');
    });
    return () => {
      void JarvisAssist.stop();
    };
  }, [isOpen, native]);

  useEffect(() => {
    if (typing) inputRef.current?.focus();
  }, [typing]);

  const close = useCallback(() => {
    void JarvisAssist.stop();
    onClose();
  }, [onClose]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || busy) return;
    setBusy(true);
    setError('');
    setHeard(text);
    setSaid('');
    setDraft('');
    onCommand?.(text);
    try {
      const data = await api.chat(text, undefined, userId, false);
      const answer = replyText(data);
      setSaid(answer || 'I did not get an answer to that.');
      if (answer && native) void JarvisAssist.speak({ text: answer }).catch(() => {});
    } catch (e) {
      setError((e as Error)?.message || 'That did not reach the house.');
    } finally {
      setBusy(false);
    }
  }, [busy, draft, native, onCommand, userId]);

  if (!isOpen) return null;

  const rings = state === 'listening' || state === 'speaking';

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-[100] flex flex-col bg-black/70 backdrop-blur-3xl"
        role="dialog"
        aria-modal="true"
        aria-label="Jarvis voice assistant"
      >
        <div className="flex justify-end p-4 pt-[calc(1rem+var(--safe-top))]">
          <button onClick={close} className="rounded-full p-3 text-slate-300 hover:bg-white/10" aria-label="Close assistant">
            <X size={22} />
          </button>
        </div>

        <div className="flex flex-1 flex-col items-center justify-center gap-6 px-6">
          <div className="relative flex h-40 w-40 items-center justify-center">
            {rings &&
              [0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="absolute rounded-full border border-white/20"
                  style={{
                    width: `${88 + i * 18 + level * 0.5}px`,
                    height: `${88 + i * 18 + level * 0.5}px`,
                    opacity: 0.5 - i * 0.12,
                    transition: 'width 120ms linear, height 120ms linear',
                  }}
                />
              ))}
            <div className="glass-panel flex h-24 w-24 items-center justify-center rounded-full">
              {state === 'thinking' ? (
                <Loader2 className="animate-spin text-slate-200" size={30} />
              ) : (
                <Mic className={state === 'listening' ? 'text-emerald-300' : 'text-slate-200'} size={30} />
              )}
            </div>
          </div>

          <p className="text-sm font-medium tracking-wide text-slate-300" aria-live="polite">
            {STATE_WORDS[state]}
          </p>

          {heard && <p className="max-w-md text-center text-lg text-white">{heard}</p>}
          {said && <p className="max-w-md text-center text-base leading-relaxed text-slate-200" aria-live="polite">{said}</p>}
          {error && <p className="max-w-md text-center text-sm text-amber-300/90">{error}</p>}

          {typing && (
            <form
              className="flex w-full max-w-md items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void send();
              }}
            >
              <input
                ref={inputRef}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Ask Jarvis…"
                className="glass-card min-h-11 flex-1 rounded-xl px-4 text-base text-white placeholder:text-slate-400"
                aria-label="Ask Jarvis"
              />
              <button
                type="submit"
                disabled={busy || !draft.trim()}
                className="min-h-11 min-w-11 rounded-xl bg-white/10 text-white disabled:opacity-40"
                aria-label="Send"
              >
                <Send size={18} className="mx-auto" />
              </button>
            </form>
          )}
        </div>

        <div className="flex items-center justify-center gap-3 p-6 pb-[calc(1.5rem+var(--safe-bottom))]">
          {native && !typing && (
            <>
              <button
                onClick={() => {
                  void JarvisAssist.stopSpeaking();
                  setState('listening');
                }}
                className="glass-card flex min-h-11 items-center gap-2 rounded-full px-5 text-sm text-slate-100"
              >
                <Square size={16} /> Stop talking
              </button>
              <button
                onClick={() => {
                  void JarvisAssist.stop().finally(() => setState('idle'));
                  setTyping(true);
                }}
                className="glass-card min-h-11 rounded-full px-5 text-sm text-slate-100"
              >
                Type instead
              </button>
            </>
          )}
          {native && typing && (
            <button
              onClick={() => {
                setTyping(false);
                setError('');
                setState('listening');
                void JarvisAssist.start({ wake: false }).catch((e: Error) => setError(e?.message || ''));
              }}
              className="glass-card flex min-h-11 items-center gap-2 rounded-full px-5 text-sm text-slate-100"
            >
              <Mic size={16} /> Talk instead
            </button>
          )}
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
