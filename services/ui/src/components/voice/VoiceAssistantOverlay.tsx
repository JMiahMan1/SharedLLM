import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Capacitor } from '@capacitor/core';
import { Check, Keyboard, Mic, Send, Sparkles, Square, X } from 'lucide-react';
import { JarvisAssist, askJarvis, type AssistEvent, type AssistState } from '../../plugins/jarvisAssist';
import JarvisHead from './JarvisHead';
import { faceStateFor } from './jarvisFace';

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
  userId?: string;
}

export default function VoiceAssistantOverlay({ isOpen, onClose, userId }: Props) {
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
  /** A brief flourish after a turn: a smile for a good answer, a worried face for a failure. */
  const [reaction, setReaction] = useState<'happy' | 'error' | null>(null);
  const reactionTimer = useRef<number | null>(null);
  const native = Capacitor.isNativePlatform();
  const scroller = useRef<HTMLDivElement | null>(null);

  const flash = useCallback((kind: 'happy' | 'error', ms: number) => {
    setReaction(kind);
    if (reactionTimer.current !== null) window.clearTimeout(reactionTimer.current);
    reactionTimer.current = window.setTimeout(() => {
      reactionTimer.current = null;
      setReaction(null);
    }, ms);
  }, []);

  useEffect(
    () => () => {
      if (reactionTimer.current !== null) window.clearTimeout(reactionTimer.current);
    },
    [],
  );

  // The last answer, so the smile can wait for the voice to finish (a flash on
  // the reply landed in the middle of the sentence being spoken).
  const saidRef = useRef('');
  useEffect(() => {
    saidRef.current = said;
  }, [said]);

  // Listen, then speak. The listener has to exist before the microphone opens:
  // registering it while `start()` was already running meant the first states of
  // a turn went to nobody, so a tapped button could look like nothing happened.
  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    let handle: { remove: () => Promise<void> } | undefined;
    const onEvent = (event: AssistEvent) => {
      if (cancelled) return;
      if (event.type === 'state' && event.text) {
        const next = event.text as AssistState;
        setState(next);
        // A new turn starts from a clean sheet: the previous answer, its error
        // and the words of the question before it must not sit under this turn.
        if (next === 'listening') {
          setSaid('');
          setHeard('');
          setError('');
          setReaction(null);
        }
        if (next === 'idle' && saidRef.current) flash('happy', 1800);
      }
      if (event.type === 'level') setLevel(typeof event.level === 'number' ? event.level : 0);
      if (event.type === 'partial' && event.text) {
        // Words as they are spoken: the orb stops looking dead while the
        // person is still talking. The finished take replaces them.
        setHeard(event.text);
        setError('');
      }
      if (event.type === 'transcript' && event.text) {
        setHeard(event.text);
        setSaid('');
        setError('');
      }
      if (event.type === 'sentence' && event.text) setSaid((prev) => (prev ? `${prev} ${event.text}` : String(event.text)));
      if (event.type === 'reply' && event.text) setSaid(event.text);
      if (event.type === 'status' && event.text) setNote(event.text);
      if (event.type === 'error') {
        setError(event.text || 'That did not work.');
        setState('idle');
        flash('error', 2400);
      }
    };
    void (async () => {
      setError('');
      setNote('');
      setHeard('');
      setSaid('');
      setLevel(0);
      setTyping(false);
      setReaction(null);
      try {
        handle = await JarvisAssist.addListener('assist', onEvent);
      } catch {
        // The web turn cannot fail to register; a native-only event stream could.
      }
      if (cancelled) {
        // Closed while the listener was resolving: drop it instead of leaking
        // one per open and close of the sheet.
        void handle?.remove();
        return;
      }
      setState('listening');
      try {
        await JarvisAssist.start({ wake: false, userId });
      } catch {
        setState('idle');
        setTyping(true);
        setError('This device will not open the microphone. Type instead.');
      }
    })();
    return () => {
      cancelled = true;
      void handle?.remove();
    };
  }, [isOpen, userId, flash]);

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
  const face = faceStateFor(state, reaction);

  /** The one control in the middle, named and drawn for what the screen is doing. */
  const primary: { label: string; icon: React.ReactNode } =
    state === 'listening'
      ? { label: 'Done speaking', icon: <Check size={28} /> }
      : state === 'thinking'
        ? { label: 'Cancel the question', icon: <X size={28} /> }
        : state === 'speaking'
          ? { label: 'Stop talking', icon: <Square size={26} fill="currentColor" /> }
          : { label: 'Tap to speak', icon: <Mic size={28} /> };

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

          <div className="relative flex shrink-0 items-center justify-between px-[calc(1rem+var(--safe-left))] pt-[calc(0.75rem+var(--safe-top))] pr-[calc(1rem+var(--safe-right))]">
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

          {/* the face, and what it heard and says */}
          <div className="relative flex min-h-0 flex-1 flex-col items-center gap-4 px-6">
            <div className="relative grid shrink-0 place-items-center py-2">
              {speaking &&
                [0, 0.35, 0.7].map((delay) => (
                  <motion.span
                    key={delay}
                    aria-hidden
                    className="absolute h-40 w-40 rounded-full border border-white/15"
                    initial={{ scale: 0.9, opacity: 0.5 }}
                    animate={{ scale: 2.1, opacity: 0 }}
                    transition={{ duration: 2.4, repeat: Infinity, delay, ease: 'easeOut' }}
                  />
                ))}
              <JarvisHead state={face} level={level} size={152} />
            </div>

            <div className="flex shrink-0 flex-col items-center gap-1">
              <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-white/45">{words[state]}</p>
              {note && !error && (
                <p className="max-w-xs text-center text-[10px] uppercase tracking-[0.22em] text-white/25">{note}</p>
              )}
            </div>

            {/* The words have their own area, which scrolls: the face, the
                question and the answer used to share one column, so a long
                answer pushed the buttons off a short screen. */}
            <div
              ref={scroller}
              data-testid="voice-transcript"
              className="min-h-0 w-full max-w-md flex-1 space-y-3 overflow-y-auto overscroll-contain pb-1 text-center [scrollbar-width:none]"
            >
              {heard && <p className="text-xl font-medium leading-snug text-white/95 sm:text-2xl">{heard}</p>}
              {said && (
                <p aria-live="polite" className="text-base leading-relaxed text-white/70">
                  {said}
                </p>
              )}
            </div>
          </div>

          {/* controls */}
          <div className="relative flex shrink-0 flex-col items-center gap-3 px-6 pb-[calc(1.5rem+var(--safe-bottom))]">
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
                  type="button"
                  aria-label="Speak instead"
                  onClick={() => {
                    setTyping(false);
                    setError('');
                    setState('listening');
                    void JarvisAssist.start({ wake: false });
                  }}
                  className="grid h-12 w-12 place-items-center rounded-full bg-white/10 text-white transition-colors hover:bg-white/20"
                >
                  <Mic size={18} />
                </button>
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
                  onClick={() => {
                    // Writing closes the microphone: leaving it open recorded a
                    // take nobody meant to make and answered the room.
                    void JarvisAssist.stop();
                    setTyping(true);
                  }}
                  aria-label="Type instead"
                  className="grid h-16 w-16 place-items-center rounded-full border border-white/10 bg-white/5 text-white/65 transition-colors hover:bg-white/10 hover:text-white"
                >
                  <Keyboard size={22} />
                </button>
                {/* One button, and what it does is what the screen is doing: a
                    square that closed the sheet while he was waiting looked
                    backwards, and stopping a recording took a second tap on a
                    different button to get the answer. */}
                <button
                  onClick={() => {
                    if (state === 'listening') {
                      // Done: this take is the question.
                      void JarvisAssist.finish?.();
                      return;
                    }
                    if (state === 'thinking') {
                      // Cancel: drop the request and stay here, ready to ask again.
                      void JarvisAssist.stop();
                      return;
                    }
                    if (state === 'speaking') {
                      // Stop the voice; the answer stays on screen to read.
                      void JarvisAssist.stopSpeaking();
                      return;
                    }
                    // Idle: ask. Nothing re-arms afterwards, so the answer stays.
                    setError('');
                    setTyping(false);
                    setState('listening');
                    void JarvisAssist.start({ wake: false, userId }).catch(() => {
                      setState('idle');
                      setError('This device will not open the microphone.');
                    });
                  }}
                  aria-label={primary.label}
                  data-testid="voice-primary"
                  data-state={state}
                  className="grid h-20 w-20 place-items-center rounded-full bg-white text-[#0b0d12] shadow-xl transition-transform active:scale-95"
                >
                  {primary.icon}
                </button>
                <span aria-hidden className="h-16 w-16" />
              </div>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
