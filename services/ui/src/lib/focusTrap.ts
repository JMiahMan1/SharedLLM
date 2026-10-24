/** Everything a keyboard user can land on inside a dialog. */
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
