import { Decoration, type DecorationSet, EditorView, ViewPlugin, ViewUpdate } from '@codemirror/view';
import { RangeSetBuilder } from '@codemirror/state';

// Lightweight unified-diff highlighting: tints added/removed/hunk lines so a
// git diff is readable when shown full-size in the editor (replaces the old
// cramped <pre> panel). No external diff package required.
function buildDecorations(view: EditorView): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>();
  for (let i = 1; i <= view.state.doc.lines; i++) {
    const line = view.state.doc.line(i);
    const text = line.text;
    let deco: Decoration | null = null;
    if (text.startsWith('+') && !text.startsWith('+++')) {
      deco = Decoration.line({ attributes: { class: 'cm-diff-add' } });
    } else if (text.startsWith('-') && !text.startsWith('---')) {
      deco = Decoration.line({ attributes: { class: 'cm-diff-del' } });
    } else if (text.startsWith('@@')) {
      deco = Decoration.line({ attributes: { class: 'cm-diff-hunk' } });
    }
    if (deco) builder.add(line.from, line.from, deco);
  }
  return builder.finish();
}

export const diffHighlight = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet;
    constructor(view: EditorView) {
      this.decorations = buildDecorations(view);
    }
    update(u: ViewUpdate) {
      if (u.docChanged || u.viewportChanged) {
        this.decorations = buildDecorations(u.view);
      }
    }
  },
  {
    decorations: (v) => v.decorations,
  },
);

export const diffTheme = EditorView.baseTheme({
  '.cm-diff-add': { backgroundColor: 'rgba(34,197,94,0.16)' },
  '.cm-diff-del': { backgroundColor: 'rgba(239,68,68,0.16)' },
  '.cm-diff-hunk': { color: '#60a5fa', fontWeight: '700' },
});
