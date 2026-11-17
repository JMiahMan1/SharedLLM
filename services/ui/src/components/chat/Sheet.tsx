import { type ReactNode } from 'react';
import { X } from 'lucide-react';
import { useDialogFocus } from '../../lib/focusTrap';

/** A bottom sheet on a phone, a centred panel on a wide screen. */
export default function Sheet({ title, onClose, children, testId, wide = false }: { title: string; onClose: () => void; children: ReactNode; testId?: string; wide?: boolean }) {
  // A dialog that does not take focus lets Tab walk into the page behind it and
  // leaves the reader with no idea where they are. Take focus on open, keep it
  // inside, and hand it back to whatever opened the sheet on close.
  //
  // Once, on open: the chat passes an inline `onClose`, and the chat re-renders
  // on every poll. Re-running the focus effect for each new `onClose` pulled
  // focus out of the field being typed in (closing a phone's keyboard) every
  // few seconds, in the new group's name, the people search and the private
  // chat's password.
  const panelRef = useDialogFocus<HTMLDivElement>(true, onClose);

  return (
    <div className="fixed inset-0 z-[80] flex items-end sm:items-center sm:justify-center" role="dialog" aria-modal="true" aria-label={title}>
      <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        ref={panelRef}
        tabIndex={-1}
        className={`relative w-full ${wide ? 'sm:max-w-2xl' : 'sm:max-w-md'} max-h-[88vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl border border-white/10 bg-slate-950 p-4 pb-[max(1rem,var(--safe-bottom))] outline-none`}
        data-testid={testId}
      >
        <div className="mb-3 flex items-center gap-2">
          <h2 className="flex-1 text-base font-bold text-white">{title}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="min-h-11 min-w-11 flex items-center justify-center rounded-xl text-slate-400">
            <X size={16} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
