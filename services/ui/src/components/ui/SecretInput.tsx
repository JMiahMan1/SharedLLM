import { useState } from 'react';
import { Eye, EyeOff, Loader2 } from 'lucide-react';

interface SecretInputProps {
  value: string;
  onChange: (value: string) => void;
  /** A value is already saved on the server. */
  saved: boolean;
  /** Fetch the saved value, for the eye button. Omit to only toggle typed text. */
  onReveal?: () => Promise<string>;
  label: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}

/**
 * A password field that hides its value by default and can show it.
 *
 * A saved secret is never sent to the browser with the form, so the field
 * says "Saved" and stays empty; leaving it empty keeps the saved value. The eye
 * fetches the saved value on demand (each reveal is audited server-side) and
 * shows it without treating it as an edit: only text you change is submitted.
 */
export default function SecretInput({ value, onChange, saved, onReveal, label, placeholder, disabled, className = '' }: SecretInputProps) {
  const [visible, setVisible] = useState(false);
  const [revealed, setRevealed] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const shown = value !== '' ? value : visible && revealed !== null ? revealed : '';

  const toggle = async () => {
    if (visible) {
      setVisible(false);
      return;
    }
    if (value === '' && saved && revealed === null && onReveal) {
      setLoading(true);
      try {
        setRevealed(await onReveal());
      } catch {
        setRevealed(null);
        setLoading(false);
        return;
      }
      setLoading(false);
    }
    setVisible(true);
  };

  return (
    <div className="relative">
      <input
        type={visible ? 'text' : 'password'}
        value={shown}
        aria-label={label}
        disabled={disabled}
        autoComplete="new-password"
        spellCheck={false}
        // Editing a revealed value starts from it; typing it back unchanged is no edit.
        onChange={(event) => onChange(revealed !== null && event.target.value === revealed ? '' : event.target.value)}
        placeholder={saved ? 'Saved (leave blank to keep)' : placeholder ?? `Enter ${label}`}
        className={`glass-input w-full pr-11 ${className}`}
      />
      <button
        type="button"
        onClick={() => void toggle()}
        disabled={disabled || (!saved && value === '')}
        aria-label={visible ? `Hide ${label}` : `Show ${label}`}
        aria-pressed={visible}
        className="absolute right-2 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-lg text-slate-400 hover:bg-white/10 hover:text-slate-100 disabled:opacity-30"
      >
        {loading ? <Loader2 size={15} className="animate-spin" /> : visible ? <EyeOff size={15} /> : <Eye size={15} />}
      </button>
      {saved && value === '' && !visible && (
        <span className="pointer-events-none absolute right-12 top-1/2 -translate-y-1/2 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-300">
          Saved
        </span>
      )}
    </div>
  );
}
