import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Bold, CheckSquare, Heading2, Highlighter, Italic, List, ListOrdered, Palette, Strikethrough, Type } from 'lucide-react';
import {
  NOTE_HIGHLIGHT_COLORS,
  NOTE_TEXT_COLORS,
  NOTE_TEXT_SIZES,
  htmlToMarkdown,
  markdownToHtml,
} from '../../lib/noteRichText';

type Menu = 'color' | 'highlight' | 'size' | null;

/** The top-level block the caret is in, or null when the caret is outside the editor. */
function currentBlock(root: HTMLElement): HTMLElement | null {
  const sel = window.getSelection();
  let node: Node | null = sel && sel.rangeCount ? sel.anchorNode : null;
  while (node && node.parentNode !== root) node = node.parentNode;
  return node && node.nodeType === 1 ? (node as HTMLElement) : null;
}

/** A colour no one picks: it is how "Default" / "No highlight" are asked of the browser, then removed. */
const CLEAR = '#010203';
const CLEAR_RGB = 'rgb(1, 2, 3)';

/** Drop the "clear" colours and unwrap spans left without any style. */
function tidySpans(root: HTMLElement) {
  for (const span of Array.from(root.querySelectorAll('span, font'))) {
    const el = span as HTMLElement;
    if (el.style.color === CLEAR_RGB) el.style.removeProperty('color');
    if (el.style.backgroundColor === CLEAR_RGB) el.style.removeProperty('background-color');
    if (!el.getAttribute('style')?.trim()) el.removeAttribute('style');
    if (el.tagName === 'SPAN' && !el.attributes.length) el.replaceWith(...Array.from(el.childNodes));
  }
}

function placeCaretAtStart(el: HTMLElement) {
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(true);
  const sel = window.getSelection();
  sel?.removeAllRanges();
  sel?.addRange(range);
}

/**
 * A Keep-style note editor. People edit formatted text and tick real
 * checkboxes; the note is saved as markdown through `onChange`, so the stored
 * file stays readable by Nextcloud and its Notes clients (see
 * `lib/noteRichText.ts` for what each format becomes).
 */
export interface RichNoteEditorHandle {
  /** Add a `#label` on a new last line (Nextcloud Notes reads labels from the text). */
  appendLabel: (tag: string) => void;
}

const RichNoteEditor = forwardRef<
  RichNoteEditorHandle,
  {
    initialMarkdown: string;
    onChange: (markdown: string) => void;
    label?: string;
  }
>(function RichNoteEditor({ initialMarkdown, onChange, label = 'Note content' }, handle) {
  const ref = useRef<HTMLDivElement>(null);
  const [menu, setMenu] = useState<Menu>(null);

  useImperativeHandle(handle, () => ({
    appendLabel: (tag: string) => {
      const root = ref.current;
      if (!root) return;
      const line = document.createElement('div');
      line.textContent = `#${tag} `;
      root.appendChild(line);
      onChange(htmlToMarkdown(root));
    },
  }));

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.innerHTML = markdownToHtml(initialMarkdown);
    try {
      document.execCommand('styleWithCSS', false, 'true');
      document.execCommand('defaultParagraphSeparator', false, 'div');
    } catch {
      // Not every webview implements these; formatting still works.
    }
    // The editor owns its content after mount, like an uncontrolled field.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const emit = () => {
    if (ref.current) onChange(htmlToMarkdown(ref.current));
  };

  const run = (command: string, value?: string) => {
    ref.current?.focus();
    document.execCommand(command, false, value);
    if (ref.current) tidySpans(ref.current);
    emit();
  };

  const toggleChecklist = () => {
    const root = ref.current;
    if (!root) return;
    root.focus();
    let block = currentBlock(root);
    if (!block) {
      // No caret yet: use the empty last line, or start a new one.
      const last = root.lastElementChild as HTMLElement | null;
      if (last?.tagName === 'DIV' && !(last.textContent ?? '').trim()) {
        block = last;
      } else {
        block = document.createElement('div');
        block.innerHTML = '<br>';
        root.appendChild(block);
      }
      placeCaretAtStart(block);
    }
    if (block.tagName !== 'DIV') return; // headings and lists keep their own form
    if (block.hasAttribute('data-task')) block.removeAttribute('data-task');
    else block.setAttribute('data-task', 'false');
    emit();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const root = ref.current;
    if (!root || e.key !== 'Enter' || e.shiftKey) return;
    const block = currentBlock(root);
    // Enter on an empty checklist row ends the list, as in Keep.
    if (block?.hasAttribute('data-task') && !(block.textContent ?? '').trim()) {
      e.preventDefault();
      block.removeAttribute('data-task');
      emit();
    }
  };

  const onInput = (e: React.FormEvent<HTMLDivElement>) => {
    const root = ref.current;
    if (root && (e.nativeEvent as InputEvent).inputType === 'insertParagraph') {
      // The browser copies the row's attributes onto the new line: a new
      // checklist row always starts unticked.
      const block = currentBlock(root);
      if (block?.hasAttribute('data-task')) block.setAttribute('data-task', 'false');
    }
    emit();
  };

  const onClick = (e: React.MouseEvent<HTMLDivElement>) => {
    // The checkbox is drawn in the row's left gutter, so a tap there ticks it.
    const row = (e.target as HTMLElement).closest('[data-task]') as HTMLElement | null;
    if (!row || !ref.current?.contains(row)) return;
    if (e.clientX - row.getBoundingClientRect().left > 32) return;
    e.preventDefault();
    row.setAttribute('data-task', row.getAttribute('data-task') === 'true' ? 'false' : 'true');
    emit();
  };

  const tool = (name: string, icon: React.ReactNode, onPress: () => void, pressed?: boolean) => (
    <button
      type="button"
      aria-label={name}
      title={name}
      aria-pressed={pressed}
      // Keep the selection while the button is pressed.
      onMouseDown={(e) => e.preventDefault()}
      onClick={onPress}
      className="flex h-10 w-10 items-center justify-center rounded-lg text-slate-300 hover:bg-white/10"
    >
      {icon}
    </button>
  );

  return (
    <div className="space-y-2">
      <div
        ref={ref}
        role="textbox"
        aria-multiline="true"
        aria-label={label}
        contentEditable
        suppressContentEditableWarning
        spellCheck
        onInput={onInput}
        onKeyDown={onKeyDown}
        onClick={onClick}
        className="note-rich glass-input min-h-48 max-h-[50vh] w-full overflow-y-auto p-3 text-base sm:text-sm"
      />
      <div role="toolbar" aria-label="Formatting" className="flex flex-wrap items-center gap-0.5">
        {tool('Bold', <Bold size={16} />, () => run('bold'))}
        {tool('Italic', <Italic size={16} />, () => run('italic'))}
        {tool('Strikethrough', <Strikethrough size={16} />, () => run('strikeThrough'))}
        {tool('Heading', <Heading2 size={16} />, () => {
          const block = ref.current ? currentBlock(ref.current) : null;
          run('formatBlock', block?.tagName === 'H2' ? 'div' : 'h2');
        })}
        {tool('Bulleted list', <List size={16} />, () => run('insertUnorderedList'))}
        {tool('Numbered list', <ListOrdered size={16} />, () => run('insertOrderedList'))}
        {tool('Checklist', <CheckSquare size={16} />, toggleChecklist)}
        {tool('Text colour', <Palette size={16} />, () => setMenu(menu === 'color' ? null : 'color'), menu === 'color')}
        {tool('Highlight', <Highlighter size={16} />, () => setMenu(menu === 'highlight' ? null : 'highlight'), menu === 'highlight')}
        {tool('Text size', <Type size={16} />, () => setMenu(menu === 'size' ? null : 'size'), menu === 'size')}
      </div>
      {menu && (
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label={menu === 'color' ? 'Text colours' : menu === 'highlight' ? 'Highlight colours' : 'Text sizes'}>
          {menu === 'color' &&
            NOTE_TEXT_COLORS.map((c) => (
              <button
                key={c.id}
                type="button"
                aria-label={`Text ${c.label}`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  run('foreColor', c.value || CLEAR);
                  setMenu(null);
                }}
                className="flex h-10 w-10 items-center justify-center"
              >
                <span className="h-6 w-6 rounded-full border border-white/30" style={{ background: c.value || 'transparent' }} />
              </button>
            ))}
          {menu === 'highlight' &&
            NOTE_HIGHLIGHT_COLORS.map((c) => (
              <button
                key={c.id}
                type="button"
                aria-label={c.id === 'none' ? c.label : `Highlight ${c.label}`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  run('hiliteColor', c.value || CLEAR);
                  setMenu(null);
                }}
                className="flex h-10 w-10 items-center justify-center"
              >
                <span className="h-6 w-6 rounded-full border border-white/30" style={{ background: c.value || 'transparent' }} />
              </button>
            ))}
          {menu === 'size' &&
            NOTE_TEXT_SIZES.map((s) => (
              <button
                key={s.id}
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  run('fontSize', s.command);
                  setMenu(null);
                }}
                className="glass-button min-h-10 px-3 py-1 text-xs"
              >
                {s.label}
              </button>
            ))}
        </div>
      )}
    </div>
  );
});

export default RichNoteEditor;
