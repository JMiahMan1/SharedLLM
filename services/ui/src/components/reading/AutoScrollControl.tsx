import { useState } from 'react';
import { ChevronsDown, Minus, Pause, Play, Plus, X } from 'lucide-react';
import { SPEEDS, useAutoScroll } from '../../lib/autoScroll';

interface AutoScrollControlProps {
  /** The element that scrolls (looked up when a run starts). */
  target: () => HTMLElement | null;
  /** Hold the scroll from outside, e.g. while a sheet is open over the text. */
  paused?: boolean;
  /**
   * ``fixed``: floats over the screen, clear of the bottom bar (the reader and focus mode).
   * ``absolute``: floats in the corner of a positioned parent (a reader with its own footer).
   */
  placement?: 'fixed' | 'absolute';
  testId?: string;
}

const BUTTON = 'flex min-h-11 min-w-11 items-center justify-center rounded-full text-slate-100 hover:bg-white/10 disabled:opacity-30';

const POSITION = {
  // Clear of the app's bottom bar, or of the system's gesture bar where there is none.
  fixed: 'fixed right-3 bottom-[calc(max(var(--bottom-bar),env(safe-area-inset-bottom,0px))+0.75rem)] sm:right-5',
  absolute: 'absolute right-3 bottom-3',
} as const;

/**
 * The auto scroll button for reading surfaces. Closed, it is one labelled
 * button; once started it opens into play/pause, slower, the speed, faster
 * and close, all thumb-sized. See ``lib/autoScroll`` for how it behaves.
 */
export default function AutoScrollControl({ target, paused = false, placement = 'fixed', testId = 'auto-scroll' }: AutoScrollControlProps) {
  const scroll = useAutoScroll(target, paused);
  const [open, setOpen] = useState(false);
  const shell = `${POSITION[placement]} z-30 rounded-full border border-white/15 bg-slate-950/90 shadow-lg backdrop-blur`;
  // Taps on the control are not the reader taking hold of the page.
  const keep = (event: { stopPropagation(): void }) => event.stopPropagation();

  if (!open) {
    return (
      <div className={shell} onPointerDown={keep} onTouchStart={keep} data-testid={testId}>
        <button
          type="button"
          onClick={() => {
            setOpen(true);
            scroll.start();
          }}
          className="flex min-h-11 items-center gap-1.5 rounded-full px-3.5 text-sm font-medium text-slate-100 hover:bg-white/10"
          data-testid={`${testId}-open`}
        >
          <ChevronsDown size={17} aria-hidden />
          Auto scroll
        </button>
      </div>
    );
  }

  const state = scroll.ended ? 'End of the text' : !scroll.running ? 'Paused' : scroll.held ? 'Held' : 'Scrolling';
  return (
    <div
      className={`${shell} flex items-center gap-0.5 p-0.5`}
      role="group"
      aria-label="Auto scroll"
      onPointerDown={keep}
      onTouchStart={keep}
      data-testid={testId}
      data-state={state.toLowerCase().replace(/ /g, '-')}
    >
      <button
        type="button"
        onClick={scroll.toggle}
        aria-label={scroll.running ? 'Pause auto scroll' : scroll.ended ? 'Start again from the top' : 'Resume auto scroll'}
        className={BUTTON}
        data-testid={`${testId}-toggle`}
      >
        {scroll.running ? <Pause size={17} /> : <Play size={17} />}
      </button>
      <button type="button" onClick={scroll.slower} disabled={scroll.speed === 0} aria-label="Slower" className={BUTTON} data-testid={`${testId}-slower`}>
        <Minus size={16} />
      </button>
      <span className="min-w-14 text-center text-xs tabular-nums leading-tight text-slate-200" aria-live="polite" data-testid={`${testId}-speed`}>
        Speed {scroll.speed + 1}
        <span className="block text-[10px] text-slate-400">{state}</span>
      </span>
      <button
        type="button"
        onClick={scroll.faster}
        disabled={scroll.speed === SPEEDS.length - 1}
        aria-label="Faster"
        className={BUTTON}
        data-testid={`${testId}-faster`}
      >
        <Plus size={16} />
      </button>
      <button
        type="button"
        onClick={() => {
          scroll.stop();
          setOpen(false);
        }}
        aria-label="Close auto scroll"
        className={BUTTON}
        data-testid={`${testId}-close`}
      >
        <X size={16} />
      </button>
    </div>
  );
}
