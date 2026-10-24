import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import { BookOpen, Copy, ExternalLink, X } from 'lucide-react';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import { PHONE_QUERY, useMediaQuery } from '../../hooks/useMediaQuery';
import { errorText } from '../../lib/errorText';

/**
 * The most verses a popover shows, the same cap Blue Letter Bible's
 * ScriptTagger uses: enough for a short passage, and anything longer belongs
 * in the reader rather than a bubble.
 */
export const POPOVER_VERSE_CAP = 7;

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])';

interface ReferenceChipProps {
  /** A reference the server's parser accepted, e.g. "Romans 5:8". */
  reference: string;
  /** The translation to quote; blank means the server's default. */
  version?: string;
  /** Open the passage in the reader. Without it the popover offers only Copy. */
  onOpenRef?: (ref: string) => void;
  className?: string;
}

interface Placement {
  top?: number;
  bottom?: number;
  left: number;
}

const POPOVER_WIDTH = 352;
const GUTTER = 16;

/** Below the chip if it fits, above it otherwise, never off the side of the screen. */
function placeNear(chip: HTMLElement): Placement {
  const rect = chip.getBoundingClientRect();
  const left = Math.max(GUTTER, Math.min(rect.left, window.innerWidth - POPOVER_WIDTH - GUTTER));
  const roomBelow = window.innerHeight - rect.bottom;
  return roomBelow >= 320 || roomBelow >= rect.top
    ? { top: rect.bottom + 8, left }
    : { bottom: window.innerHeight - rect.top + 8, left };
}

/**
 * A Scripture reference as a chip that shows its passage when tapped.
 *
 * Tapping (or pressing Enter/Space on) the chip opens a popover with the
 * verses themselves, fetched from `/passages` in the reader's translation, so
 * a reader can see what Jarvis is pointing at without leaving the answer.
 * From there they can open it in the reader or copy it. Long passages show
 * the first seven verses and say how many there are, with "More" to Blue
 * Letter Bible (a link the server builds) and the reader for the rest.
 *
 * On a phone it is a bottom sheet (a popover beside a chip at 375px wide has
 * nowhere to go); from `md` up it sits beside the chip. Either way it is a
 * modal dialog: focus moves into it, Tab stays inside it, Escape or a tap
 * outside closes it, and focus returns to the chip.
 */
export default function ReferenceChip({ reference, version, onOpenRef, className }: ReferenceChipProps) {
  const { trigger } = useHaptics();
  const phone = useMediaQuery(PHONE_QUERY);
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState<Placement | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const chipRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const headingId = useId();

  const passage = useQuery({
    queryKey: ['bible-passage', version ?? '', reference],
    queryFn: () => api.getBiblePassage(reference, version || undefined),
    enabled: open,
    retry: 0,
  });
  const verses = passage.data?.verses ?? [];
  const shown = verses.slice(0, POPOVER_VERSE_CAP);
  const overflow = verses.length > POPOVER_VERSE_CAP;
  const blb = useQuery({
    queryKey: ['bible-blb-link', reference, version ?? '', ''],
    queryFn: () => api.getBlbLink(reference, undefined, version || undefined),
    enabled: open && overflow,
    staleTime: Infinity,
    retry: false,
  });

  const close = useCallback(() => {
    setOpen(false);
    setStatus(null);
    chipRef.current?.focus();
  }, []);

  // Focus moves into the dialog as it opens, so a keyboard or screen-reader
  // user lands on the passage rather than behind it.
  useEffect(() => {
    if (open) panelRef.current?.focus();
  }, [open]);

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      // Kept to this dialog: an action sheet underneath must not close too.
      event.stopPropagation();
      close();
      return;
    }
    if (event.key !== 'Tab' || !panelRef.current) return;
    const items = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (!items.length) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === panelRef.current)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const copy = async () => {
    const data = passage.data;
    if (!data) return;
    const label = data.version ? `${data.reference} (${data.version.toUpperCase()})` : data.reference;
    const body = data.verses.map((v) => v.text.trim()).join(' ');
    try {
      await navigator.clipboard.writeText(`${label}\n${body}`);
      void trigger('success');
      setStatus('Copied to clipboard');
    } catch (error) {
      setStatus(`Could not copy: ${errorText(error)}`);
    }
  };

  const title = passage.data?.reference || reference;
  const action =
    'min-h-11 pointer-coarse:min-h-11 flex-1 rounded-xl border border-white/10 px-3 text-sm text-slate-100 hover:bg-white/5 flex items-center justify-center gap-1.5 disabled:opacity-40';

  return (
    <>
      <button
        ref={chipRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          void trigger('light');
          if (chipRef.current) setPlacement(placeNear(chipRef.current));
          setOpen(true);
        }}
        className={
          className ??
          'min-h-11 pointer-coarse:min-h-11 rounded-xl border border-sky-400/30 bg-sky-500/10 px-2.5 text-[12px] text-sky-100 flex items-center gap-1'
        }
      >
        <BookOpen size={12} /> {reference}
      </button>
      {open &&
        createPortal(
          <div
            className={`fixed inset-0 z-[70] ${phone ? 'flex items-end bg-black/60' : ''}`}
            onClick={close}
            data-testid="reference-popover-backdrop"
          >
            <div
              ref={panelRef}
              role="dialog"
              aria-modal="true"
              aria-labelledby={headingId}
              tabIndex={-1}
              onKeyDown={onKeyDown}
              onClick={(event) => event.stopPropagation()}
              data-testid="reference-popover"
              className={`glass-panel border border-white/10 shadow-2xl shadow-black/50 p-4 space-y-3 focus:outline-none ${
                phone
                  ? 'w-full rounded-t-2xl max-h-[75vh] overflow-y-auto pb-[max(1rem,env(safe-area-inset-bottom))]'
                  : 'fixed rounded-2xl max-h-[60vh] overflow-y-auto'
              }`}
              style={phone || !placement ? undefined : { ...placement, width: POPOVER_WIDTH }}
            >
              <div className="flex items-start gap-2">
                <h3 id={headingId} className="flex-1 min-w-0 text-sm font-semibold text-amber-200">
                  {title}
                  {passage.data?.version && (
                    <span className="ml-1.5 text-[11px] font-normal text-slate-400">
                      {passage.data.version.toUpperCase()}
                    </span>
                  )}
                </h3>
                <button
                  type="button"
                  onClick={close}
                  aria-label="Close"
                  className="-mr-2 -mt-2 min-h-11 min-w-11 shrink-0 flex items-center justify-center rounded-xl text-slate-400 hover:text-white"
                >
                  <X size={16} />
                </button>
              </div>

              {passage.isLoading ? (
                <div className="space-y-2" data-testid="reference-popover-loading" aria-label="Loading the passage">
                  {[0, 1, 2].map((i) => (
                    <div key={i} className="skeleton h-4 rounded w-full" />
                  ))}
                </div>
              ) : passage.error ? (
                <div className="space-y-2" data-testid="reference-popover-error">
                  <p className="text-sm text-amber-300/90 leading-relaxed">
                    {reference} could not be loaded: {errorText(passage.error)}
                  </p>
                  <button
                    type="button"
                    onClick={() => void passage.refetch()}
                    className="glass-button px-4 py-2.5 text-sm min-h-11"
                  >
                    Try again
                  </button>
                </div>
              ) : verses.length === 0 ? (
                <p className="text-sm text-slate-400" data-testid="reference-popover-empty">
                  That passage is not in the imported text.
                </p>
              ) : (
                <div className="space-y-1.5" data-testid="reference-popover-text">
                  {shown.map((verse) => (
                    <p key={`${verse.osis}-${verse.chapter}-${verse.verse}`} className="font-serif text-[15px] leading-relaxed text-slate-100">
                      <sup className="mr-1 text-[0.65em] font-sans font-semibold text-slate-400">{verse.verse}</sup>
                      {verse.text}
                    </p>
                  ))}
                  {overflow && (
                    <p className="text-[12px] text-slate-400" data-testid="reference-popover-more">
                      Showing {POPOVER_VERSE_CAP} of {verses.length} verses.{' '}
                      {blb.data ? (
                        <a
                          href={blb.data.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={() => void api.recordBibleEvent('blb_link_tap', reference).catch(() => undefined)}
                          className="inline-flex min-h-11 items-center gap-1 text-sky-300 hover:text-sky-200"
                        >
                          More on Blue Letter Bible <ExternalLink size={12} />
                        </a>
                      ) : blb.error ? (
                        <span>Blue Letter Bible link unavailable: {errorText(blb.error)}</span>
                      ) : (
                        <span>Loading the Blue Letter Bible link…</span>
                      )}
                    </p>
                  )}
                </div>
              )}

              <div className="flex gap-2">
                {onOpenRef && (
                  <button
                    type="button"
                    onClick={() => {
                      setOpen(false);
                      onOpenRef(reference);
                    }}
                    className={action}
                    data-testid="reference-popover-open"
                  >
                    <BookOpen size={14} /> Open in reader
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => void copy()}
                  disabled={!passage.data || verses.length === 0}
                  className={action}
                  data-testid="reference-popover-copy"
                >
                  <Copy size={14} /> Copy
                </button>
              </div>
              <p role="status" aria-live="polite" className="text-[12px] text-slate-400 min-h-4">
                {status}
              </p>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
