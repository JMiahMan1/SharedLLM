import { useState } from 'react';
import type { InputHTMLAttributes } from 'react';
import { Eye, EyeOff } from 'lucide-react';

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  /** Classes for the input itself; room for the eye is added on the right. */
  className?: string;
  /** Classes for the wrapper (the input's margin, e.g. `mt-1`). */
  wrapperClassName?: string;
};

/**
 * A password input with a show/hide eye, for the fields a device or integration
 * flow asks for (a web password, an encryption key). The value is hidden until
 * the eye is pressed and never leaves the input except through `onChange`.
 */
export default function PasswordField({ className = '', wrapperClassName = '', disabled, ...input }: Props) {
  const [visible, setVisible] = useState(false);
  return (
    <span className={`relative block ${wrapperClassName}`}>
      <input {...input} disabled={disabled} type={visible ? 'text' : 'password'} className={`${className} pr-12`} />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        disabled={disabled}
        aria-label={visible ? 'Hide password' : 'Show password'}
        aria-pressed={visible}
        data-testid="password-toggle"
        className="absolute inset-y-0 right-0 flex min-h-11 w-11 items-center justify-center rounded-r-lg text-slate-400 hover:text-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-400 disabled:opacity-30"
      >
        {visible ? <EyeOff size={16} aria-hidden /> : <Eye size={16} aria-hidden />}
      </button>
    </span>
  );
}
