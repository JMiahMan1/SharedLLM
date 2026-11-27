import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Capacitor } from '@capacitor/core';
import { Keyboard, Loader2, Mic, Send, Sparkles, Square, X } from 'lucide-react';
import { JarvisAssist, askJarvis, type AssistEvent, type AssistState } from '../../plugins/jarvisAssist';

/**
 * Ask Jarvis, out loud.
 *
 * One screen, one orb, no chrome. The orb is the assistant: it breathes while it
 * waits, swells with your voice while it listens, turns while it thinks and
 * sends out rings while it talks. What it heard sits above what it is saying, so
 * a misheard question is obvious at a glance instead of a mystery.
 *
 * The turn itself runs on the household's own server - the phone records a
 * question, the gateway hears it, answers it, and the sentence being spoken is
 * synthesised while the next is still being written. Nothing leaves the house.
 */
interface Props {
  isOpen: boolean;
  onClose: () => void;
  /** Called with each transcript, so the caller can fill a search box. */
  onCommand?: (transcript: string) => void;
  userId?: string;
}

export default function VoiceAssistantOverlay({ isOpen, onClose, onCommand, userId }: Props) {
  const [state, setState] = useState<AssistState>('idle');
  const [level, setLevel] = useState(0);
  const [heard, setHeard] = useState('');
  const [said, setSaid] = useState('');
  const [error, setError] = useState('');
  /** A one-line diagnostic (microphone granted or blocked), so silence explains itself. */
  const [note, setNote] = useState('');
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState('');
  const [roleOffer, setRoleOffer] = useState(false);
  const native = Capacitor.isNativePlatform();
  const scroller = useRef<HTMLDivElement | null>(null);

  // What the service reports, however it is running (native plugin or this page).
  useEffect(() => {
    if (!isOpen) return;
    let remove: (() => void) | undefined;
    void JarvisAssist.addListener('assist', (event: AssistEvent) => {
      if (event.type === 'state' && event.text) setState(event.text as AssistState);
      if (event.type === 'level') setLevel(typeof event.level === 'number' ? event.level : 0);
      if (event.type === 'transcript' && event.text) {
        setHeard(event.text);
        onCommand?.(event.text);
      }
      if (event.type === 'sentence' && event.text) setSaid((prev) => (prev ? `${prev} ${event.text}` : String(event.text)));
      if (event.type === 'reply' && event.text) setSaid(event.text);
      if (event.type === 'status' && event.text) setNote(event.text);
      if (event.type === 'error') {
        setError(event.text || 'That did not work.');
        setState('idle');
      }
    }).then((handle) => {
      remove = () => void handle.remove();
    });
    return () => remove?.();
  }, [isOpen, onCommand]);

  // A new turn starts as the sheet opens.
  useEffect(() => {
    if (!isOpen) return;
    /* eslint-disable react-hooks/set-state-in-effect -- opening the sheet is a deliberate
       reset of the turn before it; it runs once per open, never in a render loop. */
    setError('');
    setNote('');
    setHeard('');
    setSaid('');
    setLevel(0);
    setState('listening');
    /* eslint-enable react-hooks/set-state-in-effect */
    void JarvisAssist.start({ wake: false }).catch(() => {
      setState('idle');
      setTyping(true);
      setError('This device will not open the microphone. Type instead.');
    });
  }, [isOpen]);

  // Ask once whether this phone could hand us the assistant role.
  useEffect(() => {
    if (!isOpen || !native) return;
    void JarvisAssist.assistantRole()
      .then((role) => {
        if (role.canHold && !role.held) setRoleOffer(true);
      })
      .catch(() => {});
  }, [isOpen, native]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' });
  }, [said]);

  const close = () => {
    void JarvisAssist.stop();
    onClose();
  };

  const send = async (text: string) => {
    const question = text.trim();
    if (!question) return;
    setDraft('');
    setError('');
    setSaid('');
    // The typed question runs the spoken turn, so it answers out loud the same
    // way a microphone turn does (and is cut off by the same stop button).
    await askJarvis(question, userId);
  };

  const speaking = state === 'speaking';
  const thinking = state === 'thinking';
  const listening = state === 'listening';
  const swell = listening ? 1 + Math.min(level, 100) / 260 : speaking ? 1.06 : 1;

  const words: Record<AssistState, string> = {
    idle: 'Ask me anything',
    listening: 'Listening',
    thinking: 'Thinking',
    speaking: 'Speaking',
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
          className="fixed inset-0 z-[100] flex flex-col overflow-hidden text-white"
          style={{ background: 'var(--color-bg-base, #0b0d12)' }}
          role="dialog"
          aria-modal="true"
          aria-label="Ask Jarvis"
        >
          {/* the accent glow: the only light in the room */}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                'radial-gradient(60% 45% at 50% 34%, color-mix(in srgb, var(--site-accent, #8b5cf6) 26%, transparent) 0%, transparent 70%)',
            }}
          />

          <div className="relative flex items-center justify-between px-[calc(1rem+var(--safe-left))] pt-[calc(0.75rem+var(--safe-top))] pr-[calc(1rem+var(--safe-right))]">
            <span className="inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.28em] text-white/40">
              <Sparkles size={12} /> Jarvis
            </span>
            <button
              onClick={close}
              aria-label="Close"
              className="grid h-10 w-10 place-items-center rounded-full border border-white/10 bg-white/5 text-white/60 transition-colors hover:bg-white/10 hover:text-white"
            >
              <X size={18} />
            </button>
          </div>

          {/* the orb */}
          <div className="relative flex flex-1 flex-col items-center justify-center gap-8 px-6">
            <div className="relative grid place-items-center">
              {speaking &&
                [0, 0.35, 0.7].map((delay) => (
                  <motion.span
                    key={delay}
                    aria-hidden
                    className="absolute h-44 w-44 rounded-full border border-white/15"
                    initial={{ scale: 0.9, opacity: 0.5 }}
                    animate={{ scale: 2.1, opacity: 0 }}
                    transition={{ duration: 2.4, repeat: Infinity, delay, ease: 'easeOut' }}
                  />
                ))}
              <motion.div
                aria-hidden
                className="absolute h-44 w-44 rounded-full blur-2xl"
                style={{ background: 'var(--site-accent, #8b5cf6)', opacity: speaking ? 0.55 : 0.35 }}
                animate={{ scale: swell * 1.05 }}
                transition={{ type: 'spring', stiffness: 120, damping: 18 }}
              />
              <motion.div
                className="relative grid h-44 w-44 place-items-center rounded-full shadow-2xl"
                style={{
                  background:
                    'radial-gradient(circle at 34% 28%, rgba(255,255,255,0.92) 0%, var(--site-accent, #8b5cf6) 46%, rgba(9,11,16,0.92) 100%)',
                }}
                animate={{ scale: swell, rotate: speaking ? [0, 4, -4, 0] : 0 }}
                transition={
                  speaking
                    ? { rotate: { duration: 2.6, repeat: Infinity, ease: 'easeInOut' }, scale: { type: 'spring', stiffness: 120, damping: 18 } }
                    : { type: 'spring', stiffness: 120, damping: 18 }
                }
              >
                {thinking ? (
                  <Loader2 size={40} className="animate-spin text-white/90" />
                ) : listening ? (
                  <Mic size={40} className="text-white/95" />
                ) : (
                  <Sparkles size={36} className="text-white/90" />
                )}
              </motion.div>
            </div>

            <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-white/45">{words[state]}</p>
            {note && !error && (
              <p className="text-[10px] uppercase tracking-[0.22em] text-white/25">{note}</p>
            )}

            {(heard || said) && (
              <div
                ref={scroller}
                className="max-h-[34vh] w-full max-w-md space-y-3 overflow-y-auto text-center [scrollbar-width:none]"
              >
                {heard && <p className="text-2xl font-medium leading-snug text-white/95">{heard}</p>}
                {said && (
                  <p aria-live="polite" className="text-base leading-relaxed text-white/70">
                    {said}
                  </p>
                )}
              </div>
            )}
          </div>

          {/* controls */}
          <div className="relative flex flex-col items-center gap-3 px-6 pb-[calc(1.5rem+var(--safe-bottom))]">
            {error && (
              <p className="rounded-full border border-amber-300/30 bg-amber-400/10 px-4 py-2 text-center text-sm text-amber-100">
                {error}
              </p>
            )}
            {native && roleOffer && (
              <p className="text-center text-xs text-white/40">
                Hold your home button and talk to me:{' '}
                <button
                  onClick={() => {
                    void JarvisAssist.requestAssistantRole().finally(() => setRoleOffer(false));
                  }}
                  className="text-white/70 underline underline-offset-2"
                >
                  make Jarvis your assistant
                </button>
              </p>
            )}

            {typing ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void send(draft);
                }}
                className="flex w-full max-w-md items-center gap-2"
              >
                <input
                  autoFocus
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="Ask in writing…"
                  aria-label="Ask in writing"
                  className="h-12 flex-1 rounded-full border border-white/10 bg-white/5 px-5 text-base text-white placeholder:text-white/35 focus:border-white/25 focus:outline-none"
                />
                <button
                  type="submit"
                  aria-label="Send"
                  className="grid h-12 w-12 place-items-center rounded-full bg-white/10 text-white transition-colors hover:bg-white/20"
                >
                  <Send size={18} />
                </button>
              </form>
            ) : (
              <div className="flex items-center gap-5">
                <button
                  onClick={() => setTyping(true)}
                  aria-label="Type instead"
                  className="grid h-16 w-16 place-items-center rounded-full border border-white/10 bg-white/5 text-white/65 transition-colors hover:bg-white/10 hover:text-white"
                >
                  <Keyboard size={22} />
                </button>
                <button
                  onClick={() => (speaking ? void JarvisAssist.stopSpeaking() : close())}
                  aria-label={speaking ? 'Stop talking' : 'Stop listening'}
                  className="grid h-20 w-20 place-items-center rounded-full bg-white text-[#0b0d12] shadow-xl transition-transform active:scale-95"
                >
                  <Square size={26} fill="currentColor" />
                </button>
                <button
                  onClick={() => void JarvisAssist.start({ wake: false })}
                  aria-label="Ask again"
                  className="grid h-16 w-16 place-items-center rounded-full border border-white/10 bg-white/5 text-white/65 transition-colors hover:bg-white/10 hover:text-white"
                >
                  <Mic size={22} />
                </button>
              </div>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
