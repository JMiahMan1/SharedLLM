import { Pencil, Trash2 } from 'lucide-react';
import { useDialogFocus } from '../../lib/focusTrap';
import { REACTIONS } from './privateChatModel';

/** The row of reactions: one tap reacts, a second takes it back. Shared by the message sheet and the picture viewer. */
export function ReactionRow({ mine, onReact, tone = 'sheet' }: { mine: ReadonlySet<string>; onReact: (emoji: string) => void; tone?: 'sheet' | 'viewer' }) {
  return (
    <div className="grid grid-cols-6 gap-1" role="group" aria-label="React" data-testid="private-reaction-row">
      {REACTIONS.map((r) => {
        const pressed = mine.has(r.emoji);
        return (
          <button
            key={r.kind}
            type="button"
            onClick={() => onReact(r.emoji)}
            aria-label={pressed ? `Take back ${r.label}` : `React with ${r.label}`}
            aria-pressed={pressed}
            title={r.label}
            data-testid={`private-react-${r.kind}`}
            className={`flex h-12 min-w-11 items-center justify-center rounded-2xl text-[26px] leading-none transition-transform active:scale-90 ${
              pressed
                ? 'bg-rose-500/25 ring-1 ring-inset ring-rose-300/60'
                : tone === 'viewer'
                  ? 'bg-slate-800/95 shadow-lg shadow-black/40 hover:bg-slate-700'
                  : 'bg-white/[0.05] hover:bg-white/10'
            }`}
          >
            <span aria-hidden>{r.emoji}</span>
          </button>
        );
      })}
    </div>
  );
}

interface PrivateMessageSheetProps {
  /** My reactions on this message, by emoji. */
  mine: ReadonlySet<string>;
  /** "Edited at 10:32" for an edited message, shown in the sheet. */
  editedLabel?: string;
  onReact: (emoji: string) => void;
  /** Present only for my own text messages. */
  onEdit?: () => void;
  /** Present only for my own messages. */
  onDelete?: () => void;
  onClose: () => void;
}

/**
 * What a tap on a message offers: react (either parent's message), and for
 * your own text, Edit and Delete for both of you. A bottom sheet on a phone,
 * clear of the home indicator; a small card on a wide screen.
 */
export default function PrivateMessageSheet({ mine, editedLabel, onReact, onEdit, onDelete, onClose }: PrivateMessageSheetProps) {
  const panelRef = useDialogFocus<HTMLDivElement>(true, onClose);
  return (
    <div className="fixed inset-0 z-[80] flex items-end justify-center sm:items-center sm:p-4" role="dialog" aria-modal="true" aria-label="Message" data-testid="private-message-actions">
      <button type="button" aria-label="Close" tabIndex={-1} onClick={onClose} className="absolute inset-0 bg-black/55" />
      <div
        ref={panelRef}
        tabIndex={-1}
        className="relative w-full rounded-t-3xl border border-white/10 bg-slate-900 px-3 pt-2 pb-[max(0.75rem,var(--safe-bottom))] shadow-2xl outline-none motion-safe:animate-[sheet-up_180ms_ease-out] sm:max-w-sm sm:rounded-3xl sm:pb-3"
      >
        <div className="mx-auto mb-2.5 h-1 w-10 rounded-full bg-white/20 sm:hidden" aria-hidden />
        <ReactionRow mine={mine} onReact={onReact} />
        {editedLabel && <p className="mt-2.5 px-1 text-center text-[11px] text-slate-400" data-testid="private-edited-at">{editedLabel}</p>}
        <div className="mt-2.5 overflow-hidden rounded-2xl bg-white/[0.04]">
          {onEdit && (
            <button
              type="button"
              onClick={onEdit}
              data-testid="private-message-edit"
              className="flex min-h-12 w-full items-center gap-3 px-4 text-left text-sm font-semibold text-slate-100 hover:bg-white/5"
            >
              <Pencil size={16} className="text-slate-300" aria-hidden /> Edit
            </button>
          )}
          {onDelete && (
            <button
              type="button"
              onClick={onDelete}
              data-testid="private-message-delete"
              className={`flex min-h-12 w-full items-center gap-3 px-4 text-left text-sm font-semibold text-rose-300 hover:bg-rose-500/10 ${onEdit ? 'border-t border-white/5' : ''}`}
            >
              <Trash2 size={16} aria-hidden /> Delete for both of you
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            data-testid="private-message-cancel"
            className={`flex min-h-12 w-full items-center justify-center px-4 text-sm font-semibold text-slate-300 hover:bg-white/5 ${onEdit || onDelete ? 'border-t border-white/5' : ''}`}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
