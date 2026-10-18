import { User, Users } from 'lucide-react';
import type { SendAs } from './sendAsPref';

/**
 * Admin-only "Send as" control. Defaults to the caller's own identity; the
 * Admin (default) option must be picked explicitly, and the badge makes the
 * active identity obvious while it is on.
 */
export default function SendAsSelector({
  value,
  onChange,
  disabled = false,
}: {
  value: SendAs;
  onChange: (next: SendAs) => void;
  disabled?: boolean;
}) {
  const options: Array<{ id: SendAs; label: string; icon: typeof User }> = [
    { id: 'me', label: 'Me', icon: User },
    { id: 'admin', label: 'Admin', icon: Users },
  ];
  return (
    <div
      className="flex shrink-0 items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] p-0.5"
      role="radiogroup"
      aria-label="Send as"
      aria-disabled={disabled}
      title={disabled ? 'Finish or close the current edit before switching accounts' : undefined}
      data-testid="send-as-selector"
    >
      {options.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          type="button"
          role="radio"
          aria-checked={value === id}
          disabled={disabled}
          onClick={() => {
            if (!disabled) onChange(id);
          }}
          className={`relative min-h-8 px-2.5 rounded-full text-[11px] inline-flex items-center gap-1 before:absolute before:inset-x-0 before:-inset-y-1.5 before:content-[''] disabled:opacity-50 disabled:cursor-not-allowed ${
            value === id
              ? id === 'admin'
                ? 'bg-amber-400/20 text-amber-100'
                : 'bg-purple-500/25 text-purple-100'
              : 'text-slate-400'
          }`}
        >
          <Icon size={12} /> {label}
        </button>
      ))}
      {value === 'admin' && (
        <span className="sr-only" data-testid="send-as-badge">
          Sending as Admin
        </span>
      )}
    </div>
  );
}
