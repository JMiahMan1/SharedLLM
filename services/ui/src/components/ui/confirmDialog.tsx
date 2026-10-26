import { createRoot } from 'react-dom/client';
import Modal from './Modal';

export interface ConfirmOptions {
  /** The question, e.g. "Erase everything on this tablet?". */
  title: string;
  /** What will happen, in the person's own terms. */
  body: string;
  /** The verb on the confirming button ("Erase", "Delete", "Restart"). */
  confirmLabel?: string;
  cancelLabel?: string;
  /** Draws the confirm button in red. Use it for anything irreversible. */
  danger?: boolean;
}

/**
 * A promise-based stand-in for `window.confirm`, drawn with the app's own
 * `Modal`.
 *
 * A browser confirm is unstyleable, unmovable, cannot be translated or themed,
 * and on a phone it blocks the whole WebView. The app already ships a styled
 * modal (and uses it for SOS), so a destructive action should look like the
 * rest of the app. Focus starts on Cancel - the safe answer - and Escape, the
 * backdrop and the close button all answer "no".
 *
 *   if (await confirmDialog({ title: 'Erase this device?', body: '…', confirmLabel: 'Erase', danger: true })) {
 *     wipe.mutate();
 *   }
 */
export function confirmDialog({
  title,
  body,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  danger = false,
}: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const root = createRoot(host);

    let answered = false;
    const finish = (answer: boolean) => {
      // The buttons can be clicked more than once before React unmounts them.
      if (answered) return;
      answered = true;
      root.unmount();
      host.remove();
      resolve(answer);
    };

    const Dialog = () => (
      <Modal isOpen onClose={() => finish(false)} title={title} size="sm">
        <p className="text-sm text-slate-300">{body}</p>
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            onClick={() => finish(false)}
            className="min-h-11 rounded-xl border border-white/10 bg-white/5 px-4 text-sm font-semibold text-slate-200 hover:bg-white/10"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={() => finish(true)}
            className={`min-h-11 rounded-xl px-4 text-sm font-semibold text-white ${
              danger ? 'bg-red-600 hover:bg-red-700' : 'bg-blue-600 hover:bg-blue-700'
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </Modal>
    );

    root.render(<Dialog />);
  });
}
