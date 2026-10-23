import type { Parent, Root, RootContent } from 'mdast';
import { parseSpanStyle, spanStyleText } from './noteRichText';

const OPEN = /^<span style="([^"]*)">$/;
const CLOSE = '</span>';

type AnyNode = RootContent | Root;

/**
 * Remark plugin that turns the `<span style="...">…</span>` pairs the note
 * editor writes (colour, highlight, size) into real styled spans for the
 * read-only previews. react-markdown drops raw HTML, so without this the
 * styling would vanish; only the three whitelisted properties are honoured.
 */
export function remarkNoteSpans() {
  return (tree: Root) => visit(tree);
}

function visit(node: AnyNode): void {
  const parent = node as Parent;
  if (!Array.isArray(parent.children)) return;
  wrap(parent.children as RootContent[]);
  for (const child of parent.children) visit(child);
}

function wrap(children: RootContent[]): void {
  for (let i = 0; i < children.length; i += 1) {
    const child = children[i];
    const open = child.type === 'html' ? child.value.match(OPEN) : null;
    if (!open) continue;
    const style = parseSpanStyle(open[1]);
    if (!style) continue;
    let depth = 1;
    let end = -1;
    for (let j = i + 1; j < children.length; j += 1) {
      const next = children[j];
      if (next.type !== 'html') continue;
      if (OPEN.test(next.value)) depth += 1;
      else if (next.value === CLOSE) {
        depth -= 1;
        if (depth === 0) {
          end = j;
          break;
        }
      }
    }
    if (end < 0) continue;
    const inner = children.splice(i + 1, end - i - 1);
    // Drop the closing tag, then swap the opening tag for the styled node.
    children.splice(i + 1, 1);
    children[i] = {
      type: 'emphasis',
      data: { hName: 'span', hProperties: { style: spanStyleText(style) } },
      children: inner,
    } as unknown as RootContent;
  }
}
