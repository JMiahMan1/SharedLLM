import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { FileType2, Loader2 } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { api } from '../../../services/api';
import { WordProcessorToolbar } from './WordProcessorToolbar';

export interface MarkdownEditorHandle {
  save: () => Promise<void>;
}

interface MarkdownEditorProps {
  workspaceId: string;
  path: string;
  initialMarkdown: string;
  onDirtyChange?: (dirty: boolean) => void;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function inlineToHtml(text: string): string {
  let out = escapeHtml(text);
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/__([^_]+)__/g, '<strong>$1</strong>');
  out = out.replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');
  out = out.replace(/~~([^~]+)~~/g, '<s>$1</s>');
  out = out.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, '<img src="$2" alt="$1" />');
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2">$1</a>');
  return out;
}

function markdownToHtml(md: string): string {
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const html: string[] = [];
  let para: string[] = [];
  let inCode = false;
  let codeLines: string[] = [];
  let listType: 'ul' | 'ol' | null = null;
  let listItems: string[] = [];
  let inQuote = false;
  let quoteLines: string[] = [];

  const flushPara = () => {
    if (para.length) {
      html.push(`<p>${inlineToHtml(para.join(' '))}</p>`);
      para = [];
    }
  };
  const flushList = () => {
    if (listType && listItems.length) {
      const tag = listType === 'ol' ? 'ol' : 'ul';
      html.push(`<${tag}>${listItems.map((li) => `<li>${inlineToHtml(li)}</li>`).join('')}</${tag}>`);
    }
    listType = null;
    listItems = [];
  };
  const flushQuote = () => {
    if (inQuote && quoteLines.length) {
      html.push(`<blockquote>${markdownToHtml(quoteLines.join('\n'))}</blockquote>`);
    }
    inQuote = false;
    quoteLines = [];
  };

  for (const line of lines) {
    if (/^```/.test(line.trim())) {
      flushPara();
      flushList();
      flushQuote();
      if (inCode) {
        html.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`);
        codeLines = [];
        inCode = false;
      } else {
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeLines.push(line);
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      flushPara();
      flushList();
      if (!inQuote) inQuote = true;
      quoteLines.push(line.replace(/^\s*>\s?/, ''));
      continue;
    }
    flushQuote();
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushPara();
      flushList();
      const level = heading[1].length;
      html.push(`<h${level}>${inlineToHtml(heading[2])}</h${level}>`);
      continue;
    }
    if (/^\s*([-*_])\s*([-*_ ]*)\1\s*\1\s*$/.test(line.trim()) || /^[-*_]{3,}\s*$/.test(line.trim())) {
      flushPara();
      flushList();
      html.push('<hr />');
      continue;
    }
    const ulMatch = line.match(/^\s*[-*+]\s+(.*)$/);
    const olMatch = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ulMatch || olMatch) {
      flushPara();
      const cur = ulMatch ? 'ul' : 'ol';
      if (listType !== cur) {
        flushList();
        listType = cur;
      }
      listItems.push((ulMatch ? ulMatch[1] : olMatch![1]).trim());
      continue;
    }
    flushList();
    const trimmed = line.trim();
    if (trimmed === '') {
      flushPara();
      continue;
    }
    para.push(trimmed);
  }
  flushPara();
  flushList();
  flushQuote();
  if (inCode && codeLines.length) {
    html.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`);
  }
  return html.join('\n');
}

function htmlToMarkdown(root: HTMLElement): string {
  const out: string[] = [];
  const walk = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent ?? '';
      if (text.trim() || out.length === 0) out.push(text.replace(/\s+/g, ' '));
      return;
    }
    const el = node as HTMLElement;
    const tag = el.tagName.toLowerCase();
    const children = () => {
      const inner: string[] = [];
      for (const child of el.childNodes) {
        const before = out.length;
        walk(child);
        inner.push(out.splice(before).join(''));
      }
      return inner.join('');
    };
    switch (tag) {
      case 'h1': case 'h2': case 'h3': case 'h4': case 'h5': case 'h6': {
        out.push(`\n${'#'.repeat(Number(tag[1]))} ${children()}\n`);
        break;
      }
      case 'p': {
        const c = children();
        out.push(`\n${c.trim()}\n`);
        break;
      }
      case 'strong': case 'b': out.push(`**${children()}**`); break;
      case 'em': case 'i': out.push(`*${children()}*`); break;
      case 's': case 'del': out.push(`~~${children()}~~`); break;
      case 'code': out.push(`\`${children()}\``); break;
      case 'pre': {
        const c = children();
        out.push(`\n\`\`\`\n${c.replace(/\s+/g, ' ').trim()}\n\`\`\`\n`);
        break;
      }
      case 'a': out.push(`[${children()}](${el.getAttribute('href') ?? ''})`); break;
      case 'img': {
        const src = el.getAttribute('src') ?? '';
        const alt = el.getAttribute('alt') ?? '';
        out.push(`![${alt}](${src})`);
        break;
      }
      case 'ul': case 'ol': {
        const ordered = tag === 'ol';
        out.push('\n');
        const items = Array.from(el.children);
        items.forEach((li, i) => {
          out.push(`${ordered ? `${i + 1}.` : '-'} ${(li as HTMLElement).textContent ?? ''}\n`);
        });
        out.push('\n');
        break;
      }
      case 'li': break;
      case 'blockquote': out.push(`\n> ${children().trim()}\n`); break;
      case 'hr': out.push('\n---\n'); break;
      case 'br': out.push('\n'); break;
      case 'div': out.push(children()); break;
      default: out.push(children());
    }
  };
  walk(root);
  return out.join('').replace(/\n{3,}/g, '\n\n').trim() + '\n';
}

export const MarkdownEditor = forwardRef<MarkdownEditorHandle, MarkdownEditorProps>(function MarkdownEditor(
  { workspaceId, path, initialMarkdown, onDirtyChange },
  ref,
) {
  const editorRef = useRef<HTMLDivElement>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const dirtyRef = useRef(false);

  const markDirty = useCallback((value: boolean) => {
    dirtyRef.current = value;
    setDirty(value);
    onDirtyChange?.(value);
  }, [onDirtyChange]);

  useEffect(() => {
    if (editorRef.current) {
      editorRef.current.innerHTML = markdownToHtml(initialMarkdown);
      markDirty(false);
    }
  }, [initialMarkdown, markDirty]);

  const exec = useCallback((command: string, value?: string) => {
    editorRef.current?.focus();
    document.execCommand(command, false, value);
    markDirty(true);
  }, [markDirty]);

  const save = useCallback(async () => {
    if (!dirtyRef.current || !editorRef.current) return;
    setSaving(true);
    try {
      const md = htmlToMarkdown(editorRef.current);
      await api.writeWorkspaceFile(workspaceId, path, md);
      markDirty(false);
      toast.success(`Saved ${path}`);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Save failed';
      toast.error(`Save failed: ${msg}`);
    } finally {
      setSaving(false);
    }
  }, [workspaceId, path, markDirty]);

  useImperativeHandle(ref, () => ({ save }), [save]);

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-white/10 bg-[#0c1120]">
        <FileType2 size={14} className="text-indigo-400" />
        <span className="text-xs text-slate-400">Markdown — rich editor</span>
      </div>
      <WordProcessorToolbar dirty={dirty} saving={saving} onExec={exec} onSave={() => void save()} />
      <div className="flex-1 min-h-0 overflow-auto bg-white p-8">
        <div
          ref={editorRef}
          contentEditable
          suppressContentEditableWarning
          spellCheck={false}
          onInput={() => markDirty(true)}
          className="max-w-3xl mx-auto outline-none text-slate-900 leading-relaxed"
          style={{ fontFamily: 'Georgia, serif' }}
        />
      </div>
      {saving && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/30 z-10">
          <Loader2 size={24} className="animate-spin text-white" />
        </div>
      )}
    </div>
  );
});
