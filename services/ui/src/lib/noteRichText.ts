/**
 * Visual note editing over plain markdown files.
 *
 * The stored note stays a markdown file Nextcloud and Nextcloud Notes clients
 * read; this module only converts between that markdown and the HTML the
 * Keep-style editor shows, so people never see markdown syntax.
 *
 * What maps to plain markdown (compatible everywhere): bold `**`, italic `*`,
 * strikethrough `~~`, headings `#`..`###`, bullets, numbers and checklists
 * (`- [ ]` / `- [x]`). Font colour, highlight colour and size have no markdown
 * form, so they are stored as an inline `<span style="...">` limited to those
 * three properties; clients that do not render HTML show the tag around that
 * text, everything else reads cleanly.
 *
 * Opening a note never rewrites it silently: `richEditable` converts to HTML
 * and back and only allows the visual editor when the result is identical, so
 * notes that use anything unsupported (tables, code fences, nested lists,
 * links, quotes …) are edited as text instead of being flattened.
 */

/** Text colours offered by the toolbar (also the only ones the editor writes). */
export const NOTE_TEXT_COLORS = [
  { id: 'default', label: 'Default', value: '' },
  { id: 'red', label: 'Red', value: '#dc2626' },
  { id: 'orange', label: 'Orange', value: '#ea580c' },
  { id: 'green', label: 'Green', value: '#16a34a' },
  { id: 'blue', label: 'Blue', value: '#2563eb' },
  { id: 'purple', label: 'Purple', value: '#9333ea' },
] as const;

/** Highlight colours offered by the toolbar. */
export const NOTE_HIGHLIGHT_COLORS = [
  { id: 'none', label: 'No highlight', value: '' },
  { id: 'yellow', label: 'Yellow', value: '#fde047' },
  { id: 'green', label: 'Green', value: '#86efac' },
  { id: 'blue', label: 'Blue', value: '#93c5fd' },
  { id: 'pink', label: 'Pink', value: '#f9a8d4' },
] as const;

/** Text sizes offered by the toolbar; `value` is the stored font-size. */
export const NOTE_TEXT_SIZES = [
  { id: 'small', label: 'Small', value: '0.8em', command: '2' },
  { id: 'normal', label: 'Normal', value: '', command: '3' },
  { id: 'large', label: 'Large', value: '1.25em', command: '4' },
  { id: 'huge', label: 'Huge', value: '1.6em', command: '5' },
] as const;

import { ATTACHMENT_LINE, attachmentKind, attachmentName } from './noteAttachments';

const COLOR_VALUE = /^(#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20})$/;
const SIZE_VALUE = /^\d(\.\d{1,2})?em$/;
const SPAN_OPEN = /^<span style="([^"]*)">/;

interface SpanStyle {
  color?: string;
  background?: string;
  size?: string;
}

/** Parse a span's style attribute, or null when it carries anything else. */
export function parseSpanStyle(style: string): SpanStyle | null {
  const out: SpanStyle = {};
  let any = false;
  for (const part of style.split(';')) {
    if (!part.trim()) continue;
    const at = part.indexOf(':');
    if (at < 0) return null;
    const key = part.slice(0, at).trim().toLowerCase();
    const value = part.slice(at + 1).trim();
    if (key === 'color' && COLOR_VALUE.test(value)) out.color = value.toLowerCase();
    else if (key === 'background-color' && COLOR_VALUE.test(value)) out.background = value.toLowerCase();
    else if (key === 'font-size' && SIZE_VALUE.test(value)) out.size = value;
    else return null;
    any = true;
  }
  return any ? out : null;
}

/** The canonical stored form: fixed property order, no spaces. */
export function spanStyleText(style: SpanStyle): string {
  const parts: string[] = [];
  if (style.color) parts.push(`color:${style.color}`);
  if (style.background) parts.push(`background-color:${style.background}`);
  if (style.size) parts.push(`font-size:${style.size}`);
  return parts.join(';');
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function escapeAttr(text: string): string {
  return escapeHtml(text).replace(/"/g, '&quot;');
}

/**
 * An attachment line (`![](.attachments.4242/photo.jpg)`) as one block the
 * caret cannot enter. It keeps the markdown line verbatim, so saving writes
 * back exactly what was there; the editor draws the picture or file into it.
 */
function attachmentBlock(line: string): string {
  const link = line.slice(line.lastIndexOf('](') + 2, -1).replace(/^<|>$/g, '');
  const name = attachmentName(link);
  const icon = line.startsWith('!') || attachmentKind(name) === 'image' ? '🖼' : '📎';
  return `<div data-attachment="${escapeAttr(line)}" data-link="${escapeAttr(link)}" contenteditable="false">${escapeHtml(`${icon} ${name}`)}</div>`;
}

/** Find the `</span>` that closes the span opened just before `from`. */
function closingSpan(text: string, from: number): number {
  let depth = 1;
  let i = from;
  while (i < text.length) {
    if (text.startsWith('</span>', i)) {
      depth -= 1;
      if (depth === 0) return i;
      i += 7;
    } else if (SPAN_OPEN.test(text.slice(i))) {
      depth += 1;
      i += text.slice(i).match(SPAN_OPEN)![0].length;
    } else {
      i += 1;
    }
  }
  return -1;
}

/** Inline markdown (bold, italic, strike, styled spans) to HTML. */
function inlineToHtml(text: string): string {
  let out = '';
  let i = 0;
  while (i < text.length) {
    const rest = text.slice(i);
    const open = rest.match(SPAN_OPEN);
    if (open) {
      const style = parseSpanStyle(open[1]);
      const end = style ? closingSpan(text, i + open[0].length) : -1;
      if (style && end > 0) {
        const inner = inlineToHtml(text.slice(i + open[0].length, end));
        out += `<span style="${spanStyleText(style)}">${inner}</span>`;
        i = end + 7;
        continue;
      }
    }
    const bold = rest.match(/^\*\*(?=\S)([\s\S]*?\S)\*\*(?!\*)/);
    if (bold) {
      out += `<b>${inlineToHtml(bold[1])}</b>`;
      i += bold[0].length;
      continue;
    }
    const strike = rest.match(/^~~(?=\S)([\s\S]*?\S)~~/);
    if (strike) {
      out += `<s>${inlineToHtml(strike[1])}</s>`;
      i += strike[0].length;
      continue;
    }
    const italic = rest.match(/^\*(?=[^\s*])([\s\S]*?[^\s*])\*(?!\*)/);
    if (italic) {
      out += `<i>${inlineToHtml(italic[1])}</i>`;
      i += italic[0].length;
      continue;
    }
    out += escapeHtml(text[i]);
    i += 1;
  }
  return out;
}

const TASK_LINE = /^- \[( |x)\] (.*)$/;
const HEADING_LINE = /^(#{1,3}) (.*)$/;
const BULLET_LINE = /^- (.*)$/;
const NUMBER_LINE = /^\d+\. (.*)$/;

function normalizeMarkdown(markdown: string): string {
  return markdown.replace(/\r\n?/g, '\n').replace(/\s+$/, '');
}

/** Markdown to the HTML the editor shows (one block element per line). */
export function markdownToHtml(markdown: string): string {
  const lines = normalizeMarkdown(markdown).split('\n');
  let html = '';
  let list: 'ul' | 'ol' | null = null;
  const closeList = () => {
    if (list) html += `</${list}>`;
    list = null;
  };
  for (const line of lines) {
    if (ATTACHMENT_LINE.test(line)) {
      closeList();
      html += attachmentBlock(line);
      continue;
    }
    const task = line.match(TASK_LINE);
    const heading = line.match(HEADING_LINE);
    // Task lines also look like bullets, so they are checked first.
    const bullet = !task && line.match(BULLET_LINE);
    const number = line.match(NUMBER_LINE);
    if (bullet || number) {
      const kind = bullet ? 'ul' : 'ol';
      if (list !== kind) {
        closeList();
        html += `<${kind}>`;
        list = kind;
      }
      html += `<li>${inlineToHtml((bullet || number)![1]) || '<br>'}</li>`;
      continue;
    }
    closeList();
    if (task) {
      html += `<div data-task="${task[1] === 'x'}">${inlineToHtml(task[2]) || '<br>'}</div>`;
    } else if (heading) {
      const level = heading[1].length;
      html += `<h${level}>${inlineToHtml(heading[2]) || '<br>'}</h${level}>`;
    } else {
      html += `<div>${inlineToHtml(line) || '<br>'}</div>`;
    }
  }
  closeList();
  return html;
}

function rgbToHex(value: string): string {
  const m = value.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
  if (!m) return value.trim().toLowerCase();
  return `#${[m[1], m[2], m[3]].map((n) => Number(n).toString(16).padStart(2, '0')).join('')}`;
}

/** Browsers write sizes as keywords (execCommand) or the stored em values. */
function sizeFromCss(value: string): string | undefined {
  const keyword = value.trim().toLowerCase();
  const byKeyword: Record<string, string | undefined> = {
    'xx-small': '0.8em',
    'x-small': '0.8em',
    small: '0.8em',
    medium: undefined,
    large: '1.25em',
    'x-large': '1.6em',
    'xx-large': '1.6em',
    '-webkit-xxx-large': '1.6em',
  };
  if (keyword in byKeyword) return byKeyword[keyword];
  return SIZE_VALUE.test(keyword) ? keyword : undefined;
}

/** Wrap inline text in a marker, keeping edge spaces outside it. */
function mark(inner: string, marker: string): string {
  const m = inner.match(/^(\s*)([\s\S]*?)(\s*)$/)!;
  return m[2] ? `${m[1]}${marker}${m[2]}${marker}${m[3]}` : inner;
}

function spanOf(el: HTMLElement): SpanStyle {
  const out: SpanStyle = {};
  const color = el.style.color || el.getAttribute('color') || '';
  if (color) out.color = rgbToHex(color);
  const background = el.style.backgroundColor;
  if (background) out.background = rgbToHex(background);
  const size = el.style.fontSize ? sizeFromCss(el.style.fontSize) : undefined;
  if (size) out.size = size;
  return out;
}

function inlineToMarkdown(node: Node): string {
  if (node.nodeType === 3) return (node.textContent ?? '').replace(/\u00a0/g, ' ');
  if (node.nodeType !== 1) return '';
  const el = node as HTMLElement;
  const tag = el.tagName.toLowerCase();
  const inner = Array.from(el.childNodes).map(inlineToMarkdown).join('');
  if (tag === 'br') return '';
  if (tag === 'b' || tag === 'strong') return mark(inner, '**');
  if (tag === 'i' || tag === 'em') return mark(inner, '*');
  if (tag === 's' || tag === 'strike' || tag === 'del') return mark(inner, '~~');
  if (tag === 'span' || tag === 'font') {
    const style = spanStyleText(spanOf(el));
    // A browser may also express bold/italic/strike as span styles.
    let wrapped = style && inner.trim() ? `<span style="${style}">${inner}</span>` : inner;
    if (el.style.fontWeight === 'bold' || Number(el.style.fontWeight) >= 600) wrapped = mark(wrapped, '**');
    if (el.style.fontStyle === 'italic') wrapped = mark(wrapped, '*');
    if (el.style.textDecoration.includes('line-through')) wrapped = mark(wrapped, '~~');
    return wrapped;
  }
  return inner;
}

function joinAdjacent(text: string): string {
  // Two neighbouring formatted runs (`<b>a</b><b>b</b>`) become one.
  return text.replace(/\*\*\*\*/g, '').replace(/~~~~/g, '');
}

const BLOCKS = new Set(['div', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li']);

function hasBlockChild(el: HTMLElement): boolean {
  return Array.from(el.children).some((c) => BLOCKS.has(c.tagName.toLowerCase()));
}

function blocksToLines(parent: Node, lines: string[]): void {
  let run = '';
  let runHasContent = false;
  const flush = () => {
    if (runHasContent) lines.push(joinAdjacent(run));
    run = '';
    runHasContent = false;
  };
  for (const child of Array.from(parent.childNodes)) {
    const isBlock = child.nodeType === 1 && BLOCKS.has((child as HTMLElement).tagName.toLowerCase());
    if (!isBlock) {
      const text = inlineToMarkdown(child);
      if (text || (child.nodeType === 1 && (child as HTMLElement).tagName === 'BR')) runHasContent = true;
      run += text;
      continue;
    }
    flush();
    const el = child as HTMLElement;
    const tag = el.tagName.toLowerCase();
    if (tag === 'ul' || tag === 'ol') {
      let n = 1;
      for (const item of Array.from(el.children)) {
        const sub: string[] = [];
        if (item.tagName.toLowerCase() === 'li' && hasBlockChild(item as HTMLElement)) {
          blocksToLines(item, sub);
        } else {
          sub.push(joinAdjacent(inlineToMarkdown(item)));
        }
        const prefix = tag === 'ul' ? '- ' : `${n}. `;
        for (const [i, s] of sub.entries()) lines.push(i === 0 ? `${prefix}${s}` : s);
        n += 1;
      }
    } else if (el.hasAttribute('data-attachment')) {
      lines.push(el.getAttribute('data-attachment') ?? '');
    } else if (el.hasAttribute('data-task')) {
      const checked = el.getAttribute('data-task') === 'true';
      lines.push(`- [${checked ? 'x' : ' '}] ${joinAdjacent(Array.from(el.childNodes).map(inlineToMarkdown).join(''))}`);
    } else if (hasBlockChild(el)) {
      blocksToLines(el, lines);
    } else if (/^h[1-6]$/.test(tag)) {
      const level = Math.min(3, Number(tag[1]));
      lines.push(`${'#'.repeat(level)} ${joinAdjacent(Array.from(el.childNodes).map(inlineToMarkdown).join(''))}`);
    } else {
      lines.push(joinAdjacent(Array.from(el.childNodes).map(inlineToMarkdown).join('')));
    }
  }
  flush();
}

/** The editor's HTML back to the stored markdown. */
export function htmlToMarkdown(root: Node): string {
  const lines: string[] = [];
  blocksToLines(root, lines);
  return lines.join('\n').replace(/\s+$/, '');
}

/**
 * Markdown the visual editor does not draw (tables, fences, quotes, rules,
 * images, links, indented lists). The one exception is an attachment alone
 * on its line, which `richEditable` takes out before this test. These would survive as literal text, but
 * showing pipes and brackets defeats the point, so they open as text.
 */
const UNSUPPORTED =
  /^([ \t]*\||```|~~~|>|[*+][ \t]|#{4,6}[ \t]|[ \t]*(-{3,}|\*{3,}|_{3,})[ \t]*$|[ \t]+([-*+]|\d+\.)[ \t])|!?\[[^\]\n]*\]\([^)\n]*\)|<\/?[a-zA-Z]/m;

/**
 * True when the visual editor can open this markdown and save it back
 * unchanged. Anything else (tables, code, links, nested lists …) is edited as
 * text so the visual editor can never flatten it.
 */
export function richEditable(markdown: string): boolean {
  if (typeof document === 'undefined') return false;
  // Valid styled spans are ours; any other tag means HTML this editor would only show as text.
  const withoutSpans = markdown
    .replace(/<span style="([^"]*)">/g, (tag, style: string) => (parseSpanStyle(style) ? '' : tag))
    .replace(/<\/span>/g, '');
  // An attachment alone on its line is drawn as a picture or file block.
  const withoutAttachments = withoutSpans
    .split('\n')
    .filter((line) => !ATTACHMENT_LINE.test(line))
    .join('\n');
  if (UNSUPPORTED.test(withoutAttachments)) return false;
  const holder = document.createElement('div');
  holder.innerHTML = markdownToHtml(markdown);
  return htmlToMarkdown(holder) === normalizeMarkdown(markdown);
}
