/** Everything a keyboard user can land on inside a dialog. */
import { useEffect, useRef } from 'react';

export const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** The focusable elements inside `panel`, in tab order. */
export function focusableIn(panel: HTMLElement | null | undefined): HTMLElement[] {
  return Array.from(panel?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
}

/**
 * Keep Tab and Shift+Tab inside a dialog: from the last control Tab wraps to
 * the first, and from the first (or the panel itself, or anywhere outside)
 * Shift+Tab wraps to the last. Call it from a keydown handler; it ignores
 * every other key.
 */
export function trapTab(event: Pick<KeyboardEvent, 'key' | 'shiftKey' | 'preventDefault'>, panel: HTMLElement | null): void {
  if (event.key !== 'Tab' || !panel) return;
  const items = focusableIn(panel);
  if (items.length === 0) {
    event.preventDefault();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  if (event.shiftKey && (active === first || active === panel || !panel.contains(active))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

/**
 * Focus management for a popup that is rendered only while `open` is true.
 *
 * Returns the ref to put on the popup panel (which also needs
 * `tabIndex={-1}`). While open: focus moves into the panel, Tab stays inside
 * it, Escape calls `onClose`, and on close focus returns to whatever had it
 * before. Without this a keyboard or screen-reader user tabs out into the page
 * behind the popup and loses their place.
 */
export function useDialogFocus<T extends HTMLElement>(open: boolean, onClose?: () => void) {
  const ref = useRef<T | null>(null);

  // Callers pass an inline arrow, so `onClose` is a new function on every
  // render. Depending on it re-ran this effect on each keystroke, and its
  // cleanup then restored focus to whatever held it before the dialog opened -
  // so a text field inside a dialog kept only its first character. Hold the
  // callback in a ref and depend on the open flag alone.
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const restoreTo = document.activeElement as HTMLElement | null;
    const panel = ref.current;
    (focusableIn(panel)[0] ?? panel)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && closeRef.current) {
        closeRef.current();
        return;
      }
      trapTab(e, panel);
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      restoreTo?.focus?.();
    };
  }, [open]);

  return ref;
}
