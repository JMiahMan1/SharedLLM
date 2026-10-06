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
}: {
  value: SendAs;
  onChange: (next: SendAs) => void;
}) {
  const options: Array<{ id: SendAs; label: string; icon: typeof User }> = [
    { id: 'me', label: 'Me', icon: User },
    { id: 'admin', label: 'Admin', icon: Users },
  ];
  return (
    <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-label="Send as" data-testid="send-as-selector">
      <span className="text-[10px] uppercase tracking-wider text-slate-500">Send as</span>
      {options.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          type="button"
          role="radio"
          aria-checked={value === id}
          onClick={() => onChange(id)}
          className={`min-h-9 px-2.5 rounded-full border text-[11px] inline-flex items-center gap-1 ${
            value === id
              ? 'border-purple-400/50 bg-purple-400/10 text-purple-200'
              : 'border-white/10 bg-white/5 text-slate-400'
          }`}
        >
          <Icon size={12} /> {label}
        </button>
      ))}
      {value === 'admin' && (
        <span
          className="rounded-full border border-amber-400/40 bg-amber-400/10 px-2 py-0.5 text-[10px] text-amber-200"
          data-testid="send-as-badge"
        >
          Sending as Admin
        </span>
      )}
    </div>
  );
}
