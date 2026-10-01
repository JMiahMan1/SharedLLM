import { FC, ReactNode, useEffect } from 'react';
import { X } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  size?: string;
}

const SIZE_MAP: Record<string, string> = {
  'sm': 'max-w-sm',
  'md': 'max-w-md',
  'lg': 'max-w-lg',
  'xl': 'max-w-xl',
  '2xl': 'max-w-2xl',
  '3xl': 'max-w-3xl',
  '4xl': 'max-w-4xl',
  '5xl': 'max-w-5xl',
  '6xl': 'max-w-6xl',
  '7xl': 'max-w-7xl',
};

/**
 * Centred dialog on desktop, **bottom sheet on phones**.
 *
 * A centred dialog at 375px wide leaves ~20px of margin and traps the content
 * against the screen edges; a sheet anchored to the bottom is thumb-reachable and
 * gets the full width. This was previously centre-only, which is why Wander's
 * three trip modals (edit / share / locations) felt cramped on a phone.
 *
 * Also adds Escape-to-close and a body scroll lock, which the old version
 * lacked: Escape is an expected affordance on desktop, and without a scroll lock
 * the page behind scrolls under an open dialog.
 */
const Modal: FC<ModalProps> = ({ isOpen, onClose, title, children, size }) => {
  const maxWClass = SIZE_MAP[size || ''] || 'max-w-2xl';

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [isOpen, onClose]);

  return (
    <AnimatePresence>
      {isOpen && (
        // `items-end` + `p-0` on phones, centred with padding from `sm` up.
        <div className="fixed inset-0 z-50 flex items-end sm:items-center sm:justify-center sm:p-4">
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="absolute inset-0 bg-black/70 backdrop-blur-md"
          />
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label={title}
            initial={{ opacity: 0, y: 24, scale: 1 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 24 }}
            className={`glass-panel w-full ${maxWClass} max-h-[90vh] sm:max-h-[90vh] overflow-hidden flex flex-col relative z-10 shadow-2xl shadow-black/50 rounded-t-2xl sm:rounded-2xl safe-area-bottom`}
          >
            <div className="p-4 sm:p-6 border-b border-white/5 flex items-center justify-between bg-white/5">
              <h3 className="text-lg sm:text-xl font-bold text-white tracking-tight">{title}</h3>
              <button
                onClick={onClose}
                aria-label="Close dialog"
                className="flex items-center justify-center h-9 w-9 shrink-0 text-slate-400 hover:text-white hover:bg-white/5 rounded-xl transition-all pointer-coarse:h-11 pointer-coarse:w-11"
              >
                <X size={22} />
              </button>
            </div>
            <div className="p-4 sm:p-6 overflow-y-auto flex-1">{children}</div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
};

export default Modal;