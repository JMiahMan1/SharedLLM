import toast from 'react-hot-toast';

/** How long an Undo stays offered: long enough to notice a mis-tap, as in Keep. */
export const UNDO_TOAST_MS = 8000;

/**
 * A toast with an Undo button. Tapping Undo dismisses the toast and runs
 * `onUndo` once; letting it time out keeps the action. Returns the toast id so
 * a caller can withdraw the offer (e.g. when the action itself failed).
 */
export function showUndoToast(message: string, onUndo: () => void): string {
  let used = false;
  return toast(
    (t) => (
      <span className="flex items-center gap-3" data-testid="undo-toast">
        <span>{message}</span>
        <button
          type="button"
          className="min-h-9 rounded-lg px-2 text-sm font-semibold text-purple-300 hover:bg-white/10"
          onClick={() => {
            toast.dismiss(t.id);
            if (used) return;
            used = true;
            onUndo();
          }}
        >
          Undo
        </button>
      </span>
    ),
    { duration: UNDO_TOAST_MS },
  );
}
