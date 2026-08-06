import { Bold, Italic, Underline, Strikethrough, List, ListOrdered, AlignLeft, AlignCenter, AlignRight, Undo2, Redo2, Save, Loader2 } from 'lucide-react';

const TOOLBAR_STYLE = 'px-2 py-1.5 rounded-md text-slate-300 hover:text-white hover:bg-white/10 transition-colors flex items-center justify-center';

interface WordProcessorToolbarProps {
  dirty: boolean;
  saving: boolean;
  onExec: (command: string, value?: string) => void;
  onSave: () => void;
}

// Shared contentEditable word-processor toolbar for ODF and DOCX editors.
export function WordProcessorToolbar({ dirty, saving, onExec, onSave }: WordProcessorToolbarProps) {
  const handleHeading = (level: number | null) => {
    onExec('formatBlock', level ? `H${level}` : 'P');
  };

  return (
    <div className="flex items-center gap-1 flex-wrap px-3 py-2 border-b border-white/10 bg-[#0c1120]">
      <button type="button" className={TOOLBAR_STYLE} title="Undo" onClick={() => onExec('undo')}><Undo2 size={15} /></button>
      <button type="button" className={TOOLBAR_STYLE} title="Redo" onClick={() => onExec('redo')}><Redo2 size={15} /></button>
      <span className="w-px h-5 bg-white/10 mx-1" />
      <button type="button" className={TOOLBAR_STYLE} title="Bold (Ctrl+B)" onClick={() => onExec('bold')}><Bold size={15} /></button>
      <button type="button" className={TOOLBAR_STYLE} title="Italic (Ctrl+I)" onClick={() => onExec('italic')}><Italic size={15} /></button>
      <button type="button" className={TOOLBAR_STYLE} title="Underline (Ctrl+U)" onClick={() => onExec('underline')}><Underline size={15} /></button>
      <button type="button" className={TOOLBAR_STYLE} title="Strikethrough" onClick={() => onExec('strikeThrough')}><Strikethrough size={15} /></button>
      <span className="w-px h-5 bg-white/10 mx-1" />
      <select
        className="px-1.5 py-1 rounded-md bg-slate-900 border border-white/10 text-xs text-slate-200"
        defaultValue="p"
        onChange={(e) => handleHeading(e.target.value === 'p' ? null : parseInt(e.target.value, 10))}
        title="Paragraph style"
      >
        <option value="p">Normal</option>
        <option value="1">Heading 1</option>
        <option value="2">Heading 2</option>
        <option value="3">Heading 3</option>
        <option value="4">Heading 4</option>
        <option value="5">Heading 5</option>
        <option value="6">Heading 6</option>
      </select>
      <select
        className="px-1.5 py-1 rounded-md bg-slate-900 border border-white/10 text-xs text-slate-200"
        defaultValue="3"
        onChange={(e) => onExec('fontSize', e.target.value)}
        title="Font size"
      >
        {[
          ['1', 'Tiny'],
          ['2', 'Small'],
          ['3', 'Normal'],
          ['4', 'Large'],
          ['5', 'Larger'],
          ['6', 'XL'],
          ['7', 'XXL'],
        ].map(([v, label]) => (
          <option key={v} value={v}>{label}</option>
        ))}
      </select>
      <span className="w-px h-5 bg-white/10 mx-1" />
      <button type="button" className={TOOLBAR_STYLE} title="Bullet list" onClick={() => onExec('insertUnorderedList')}><List size={15} /></button>
      <button type="button" className={TOOLBAR_STYLE} title="Numbered list" onClick={() => onExec('insertOrderedList')}><ListOrdered size={15} /></button>
      <span className="w-px h-5 bg-white/10 mx-1" />
      <button type="button" className={TOOLBAR_STYLE} title="Align left" onClick={() => onExec('justifyLeft')}><AlignLeft size={15} /></button>
      <button type="button" className={TOOLBAR_STYLE} title="Align center" onClick={() => onExec('justifyCenter')}><AlignCenter size={15} /></button>
      <button type="button" className={TOOLBAR_STYLE} title="Align right" onClick={() => onExec('justifyRight')}><AlignRight size={15} /></button>
      <span className="w-px h-5 bg-white/10 mx-1" />
      <button
        type="button"
        onClick={onSave}
        disabled={saving || !dirty}
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-40 transition-colors ml-auto"
        title="Save (Ctrl+S)"
      >
        {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
        Save
      </button>
    </div>
  );
}
