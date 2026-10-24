/**
 * True when a key event comes from somewhere the person is typing (a field,
 * a select, or a contentEditable editor), where a single-key shortcut must
 * not fire.
 */
export function isTypingTarget(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  if (!element || typeof element.tagName !== 'string') return false;
  const tag = element.tagName.toLowerCase();
  return (
    element.isContentEditable
    || tag === 'input'
    || tag === 'textarea'
    || tag === 'select'
    || tag === 'option'
  );
}
