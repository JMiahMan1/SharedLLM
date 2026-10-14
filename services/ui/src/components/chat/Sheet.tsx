import type { ReactNode } from 'react';
import { X } from 'lucide-react';

/** A bottom sheet on a phone, a centred panel on a wide screen. */
export default function Sheet({ title, onClose, children, testId }: { title: string; onClose: () => void; children: ReactNode; testId?: string }) {
  return (
    <div className="fixed inset-0 z-[80] flex items-end sm:items-center sm:justify-center" role="dialog" aria-modal="true" aria-label={title}>
      <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        className="relative w-full sm:max-w-md max-h-[88vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl border border-white/10 bg-slate-950 p-4 pb-[max(1rem,env(safe-area-inset-bottom))]"
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
