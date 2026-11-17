import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { X } from 'lucide-react';
import { focusableIn, trapTab } from '../../lib/focusTrap';

/**
 * Where a device's detail opens: a bottom sheet on a phone or a portrait tablet (thumb-reachable,
 * full width) and a panel along the right edge from 1024 px up, so the grid stays in view beside
 * it (Home Assistant's more-info dialog covers the page; Google Home's side panel does not).
 * Escape, the backdrop and the close button close it; focus moves in and is kept inside, and
 * goes back to the tile on close. Clear of the status bar (--safe-top) and the gesture bar
 * (--safe-bottom).
 */
export default function DeviceSheet({ title, header, onClose, children }: {
  title: string; header: ReactNode; onClose: () => void; children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement | null>(null);
  const reduce = useReducedMotion();
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; }, [onClose]);

  useEffect(() => {
    const restoreTo = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    (focusableIn(panel.current)[0] ?? panel.current)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { closeRef.current(); return; }
      trapTab(e, panel.current);
    };
    document.addEventListener('keydown', onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = overflow;
      restoreTo?.focus();
    };
  }, []);

  const wide = typeof window !== 'undefined' && window.matchMedia?.('(min-width: 1024px)').matches;
  const from = reduce ? { opacity: 0 } : wide ? { x: 32, opacity: 0 } : { y: 48, opacity: 0 };

  return createPortal(
    <AnimatePresence>
      <div className="fixed inset-0 z-50 flex items-end lg:items-stretch lg:justify-end">
        <motion.div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose}
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} aria-hidden />
        <motion.div
          ref={panel}
          role="dialog"
          aria-modal="true"
          aria-label={title}
          tabIndex={-1}
          initial={from}
          animate={{ x: 0, y: 0, opacity: 1 }}
          exit={from}
          transition={{ duration: reduce ? 0 : 0.22, ease: 'easeOut' }}
          className="glass-panel relative z-10 flex max-h-[calc(100dvh-var(--safe-top)-0.75rem)] w-full flex-col overflow-hidden rounded-b-none rounded-t-3xl border border-white/10 shadow-2xl shadow-black/50 outline-none lg:my-3 lg:mr-3 lg:max-h-[calc(100dvh-1.5rem)] lg:w-[30rem] lg:rounded-3xl"
        >
          <div className="mx-auto mt-2 h-1 w-10 shrink-0 rounded-full bg-white/20 lg:hidden" aria-hidden />
          <div className="flex shrink-0 items-start gap-3 border-b border-white/5 px-4 pb-3 pt-2 sm:px-5 lg:pt-4">
            <div className="min-w-0 flex-1">{header}</div>
            <button type="button" onClick={onClose} aria-label="Close"
              className="-mr-1 flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-slate-400 hover:bg-white/10 hover:text-white">
              <X size={20} />
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-[calc(var(--safe-bottom)+1.25rem)] pt-4 sm:px-5">
            {children}
          </div>
        </motion.div>
      </div>
    </AnimatePresence>,
    document.body,
  );
}
