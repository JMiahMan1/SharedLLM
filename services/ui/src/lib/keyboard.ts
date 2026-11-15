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

/**
 * True when a key press belongs to the keyboard's word in progress rather than
 * to the field: while a word is being composed (the underlined word on Gboard
 * and the Samsung keyboard, or an input method on a computer), and for the key
 * events a browser reports as 229 ("an input method has this key"). Treating
 * such an Enter as "send" sent the message and emptied the box while the
 * keyboard still held the last word, which it then typed back into the empty
 * box. Leave that Enter to the keyboard (it finishes the word, or adds a line).
 */
export function isImeKey(event: { keyCode?: number; nativeEvent?: { isComposing?: boolean } }): boolean {
  return Boolean(event.nativeEvent?.isComposing) || event.keyCode === 229;
}
