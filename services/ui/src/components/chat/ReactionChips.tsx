import { useEffect, useRef, useState } from 'react';
import { Loader2, X } from 'lucide-react';
import { reactorNames, type Reactor, type TalkMessage } from './chatModel';

const HOLD_MS = 450;

/**
 * The reactions under a message: one chip per emoji with how many used it.
 * Mine are lit; tapping a chip adds mine or takes it back. Holding a chip (or
 * right-clicking it) shows who reacted with what, asked from the server then,
 * so the feed never fetches names nobody looks at.
 */
export default function ReactionChips({
  message,
  counts,
  mine,
  canAct,
  myActorIds,
  onReact,
  loadReactors,
}: {
  message: TalkMessage;
  counts: Record<string, number>;
  /** Which side of the feed the message is on. */
  mine: boolean;
  canAct: boolean;
  myActorIds: ReadonlySet<string>;
  onReact: (message: TalkMessage, emoji: string) => void;
  loadReactors?: (message: TalkMessage) => Promise<Record<string, Reactor[]>>;
}) {
  const signature = JSON.stringify(counts);
  // The list belongs to the counts it was asked for: once someone reacts while
  // it is open, it is out of date and closes (hold again to ask again).
  const [asked, setAsked] = useState<{ at: string; list: Record<string, Reactor[]> | 'loading' | 'failed' } | null>(null);
  const who = asked && asked.at === signature ? asked.list : null;
  const setWho = (list: Record<string, Reactor[]> | 'loading' | 'failed' | null) => setAsked(list === null ? null : { at: signature, list });
  const holdTimer = useRef<number | undefined>(undefined);
  const held = useRef(false);
  const self = new Set(message.reactions_self ?? []);

  useEffect(() => () => window.clearTimeout(holdTimer.current), []);

  // Opened near the composer, the list would sit under it: bring it on screen.
  const panelRef = useRef<HTMLDivElement | null>(null);
  const open = who !== null;
  const loaded = who !== null && who !== 'loading';
  useEffect(() => {
    if (open) panelRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
  }, [open, loaded]);

  const showWho = () => {
    if (!loadReactors) return;
    setWho('loading');
    loadReactors(message)
      .then((list) => setWho(list))
      .catch(() => setWho('failed'));
  };

  const startHold = () => {
    held.current = false;
    if (!loadReactors) return;
    window.clearTimeout(holdTimer.current);
    holdTimer.current = window.setTimeout(() => {
      held.current = true;
      showWho();
    }, HOLD_MS);
  };
  const endHold = () => window.clearTimeout(holdTimer.current);

  const entries = Object.entries(counts);
  if (!entries.length) return null;
  return (
    <div className={`relative -mt-1.5 flex flex-col px-2 ${mine ? 'items-end' : 'items-start'}`}>
      <div className={`flex flex-wrap gap-1 ${mine ? 'justify-end' : ''}`} data-testid="reaction-chips">
        {entries.map(([emoji, count]) => {
          const lit = self.has(emoji);
          return (
            <button
              key={emoji}
              type="button"
              data-testid="reaction-chip"
              aria-pressed={lit}
              aria-label={`${emoji} ${count}${lit ? ', including you' : ''}`}
              title={lit ? 'Tap to take yours back. Hold to see who reacted.' : 'Tap to react too. Hold to see who reacted.'}
              onPointerDown={startHold}
              onPointerUp={endHold}
              onPointerLeave={endHold}
              onPointerCancel={endHold}
              onContextMenu={(event) => {
                if (!loadReactors) return;
                event.preventDefault();
                held.current = true;
                showWho();
              }}
              onClick={() => {
                if (held.current) {
                  held.current = false;
                  return;
                }
                if (canAct) onReact(message, emoji);
              }}
              // A small chip under the bubble; on a phone its tap area reaches 44px above and below it.
              className={`relative inline-flex select-none items-center gap-1 rounded-full border px-2 py-0.5 text-[13px] leading-5 shadow transition active:scale-95 max-sm:before:absolute max-sm:before:inset-x-0 max-sm:before:-inset-y-2.5 max-sm:before:content-[''] ${
                lit ? 'border-purple-400/70 bg-purple-500/30 text-white' : 'border-white/10 bg-slate-800 text-slate-200 hover:bg-slate-700'
              }`}
            >
              <span aria-hidden>{emoji}</span>
              <span className="text-[11px] font-semibold tabular-nums" aria-hidden>
                {count}
              </span>
            </button>
          );
        })}
      </div>
      {who && (
        <div
          ref={panelRef}
          role="dialog"
          aria-label="Who reacted"
          data-testid="reaction-people"
          className="z-20 mt-1 w-max min-w-[11rem] max-w-[min(18rem,80vw)] rounded-2xl border border-white/10 bg-slate-900/95 p-2 text-sm shadow-xl backdrop-blur"
        >
          <div className="flex items-center justify-between gap-2 pl-1">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Reactions</span>
            <button
              type="button"
              aria-label="Close who reacted"
              onClick={() => setWho(null)}
              className="flex h-11 w-11 items-center justify-center rounded-full text-slate-400 hover:bg-white/10 sm:h-8 sm:w-8"
            >
              <X size={14} />
            </button>
          </div>
          {who === 'loading' ? (
            <p className="flex items-center gap-2 px-1 py-1 text-slate-400">
              <Loader2 size={14} className="animate-spin" /> Loading
            </p>
          ) : who === 'failed' ? (
            <p className="px-1 py-1 text-slate-400">Could not load who reacted.</p>
          ) : (
            <ul className="space-y-1">
              {entries.map(([emoji]) => {
                const names = reactorNames(who[emoji], myActorIds);
                return (
                  <li key={emoji} className="flex items-start gap-2 px-1 py-0.5" data-testid="reaction-people-row">
                    <span className="text-lg leading-6" aria-hidden>
                      {emoji}
                    </span>
                    <span className="min-w-0 flex-1 break-words leading-6 text-slate-100">
                      <span className="sr-only">{emoji} </span>
                      {names.length ? names.join(', ') : 'Nobody now'}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
