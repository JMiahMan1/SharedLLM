import { useEffect, useRef, useState, type InputHTMLAttributes } from 'react';

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'type' | 'defaultValue'> & {
  value: number | null | undefined;
  /** The number typed, or null for an empty box. Not called while the text is not a number yet ("-"). */
  onValue: (value: number | null) => void;
};

function show(value: number | null | undefined): string {
  return value === null || value === undefined || Number.isNaN(value) ? '' : String(value);
}

/**
 * A number box that keeps the text being typed.
 *
 * Bound straight to a number, a box rewrote itself on every key press: "0."
 * became "0", so a price like 0.15 could not be typed, and a field whose
 * handler fell back to a default (`Number(v) || 1`) put the 1 back the moment
 * it was emptied, so it could not be cleared and retyped ("15" for "5").
 * This keeps the text as typed, reports the number, and only shows the
 * caller's value again once the box loses focus (or the value changes while
 * nobody is typing in it).
 */
export default function NumberField({ value, onValue, onBlur, inputMode = 'decimal', ...rest }: Props) {
  const ref = useRef<HTMLInputElement | null>(null);
  const [text, setText] = useState(() => show(value));

  useEffect(() => {
    // Following a value set elsewhere, never while it is being typed in.
    if (document.activeElement !== ref.current) setText(show(value));
  }, [value]);

  return (
    <input
      {...rest}
      ref={ref}
      type="text"
      inputMode={inputMode}
      autoComplete="off"
      value={text}
      onChange={(event) => {
        const next = event.target.value;
        setText(next);
        if (next.trim() === '') onValue(null);
        else {
          const n = Number(next);
          if (Number.isFinite(n)) onValue(n);
        }
      }}
      onBlur={(event) => {
        setText(show(value));
        onBlur?.(event);
      }}
    />
  );
}
