import { useCallback, useEffect, useImperativeHandle, useRef, useState, forwardRef } from 'react';
import JSZip from 'jszip';
import { Loader2, FileType2 } from 'lucide-react';
import { api } from '../../../services/api';
import toast from 'react-hot-toast';
import { WordProcessorToolbar } from './WordProcessorToolbar';

// ── ODF namespaces (OpenDocument 1.2) ────────────────────────────────────────
const NS = {
  office: 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
  text: 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
  style: 'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
  fo: 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0',
  table: 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
  draw: 'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0',
};

export interface OdfViewerHandle {
  save: () => Promise<void>;
}

interface OdfViewerProps {
  url: string;
  workspaceId: string;
  path: string;
  onDirtyChange?: (dirty: boolean) => void;
}

// Resolve effective text-properties for a named ODF style (inheritance-aware).
function resolveStyleProps(doc: Document, styleName: string | null): Record<string, string> {
  const props: Record<string, string> = {};
  if (!styleName) return props;
  let current = styleName;
  let depth = 0;
  while (current && depth < 12) {
    const el = doc.querySelector(`[${localNameOf('style:name')}="${current}"]`);
    if (!el) break;
    const tp = el.querySelector(`[${localNameOf('style:text-properties')}]`);
    if (tp) {
      const attrs = (tp as Element).attributes;
      for (let i = 0; i < attrs.length; i++) {
        const a = attrs[i];
        if (!props[a.name]) props[a.name] = a.value;
      }
    }
    const parent = el.getAttribute(localNameOf('style:parent-style-name'));
    if (!parent) break;
    current = parent;
    depth += 1;
  }
  return props;
}

function localNameOf(qname: string): string {
  return qname.split(':')[1];
}

function styleToCss(props: Record<string, string>): Record<string, string> {
  const css: Record<string, string> = {};
  if (props['fo:font-weight'] === 'bold') css.fontWeight = 'bold';
  if (props['style:font-weight-asian'] === 'bold') css.fontWeight = 'bold';
  if (props['fo:font-style'] === 'italic') css.fontStyle = 'italic';
  if (props['style:font-style-asian'] === 'italic') css.fontStyle = 'italic';
  if (props['style:text-underline-style'] === 'solid') css.textDecoration = css.textDecoration ? css.textDecoration + ' underline' : 'underline';
  if (props['style:text-line-through-style'] === 'solid') css.textDecoration = css.textDecoration ? css.textDecoration + ' line-through' : 'line-through';
  if (props['fo:font-size']) css.fontSize = props['fo:font-size'];
  if (props['fo:font-family']) css.fontFamily = props['fo:font-family'];
  if (props['fo:color']) css.color = props['fo:color'];
  return css;
}

function cssToStyleProps(css: CSSStyleDeclaration): Record<string, string> {
  const props: Record<string, string> = {};
  if (css.fontWeight === 'bold') {
    props['fo:font-weight'] = 'bold';
    props['style:font-weight-asian'] = 'bold';
  }
  if (css.fontStyle === 'italic') {
    props['fo:font-style'] = 'italic';
    props['style:font-style-asian'] = 'italic';
  }
  const dec = css.textDecoration || '';
  if (dec.includes('underline')) props['style:text-underline-style'] = 'solid';
  if (dec.includes('line-through')) props['style:text-line-through-style'] = 'solid';
  const m = (css.fontSize || '').match(/([\d.]+)(pt|px|em|rem|%)/);
  if (m) {
    const val = m[1];
    const unit = m[2];
    props['fo:font-size'] = unit === 'pt' ? `${val}pt` : unit === 'px' ? `${Math.round((parseFloat(val) * 0.75) * 100) / 100}pt` : `${val}pt`;
  }
  if (css.fontFamily && !css.fontFamily.includes('sans-serif') && !css.fontFamily.includes('serif') && !css.fontFamily.includes('monospace')) {
    props['fo:font-family'] = css.fontFamily.split(',')[0].trim().replace(/['"]/g, '');
  }
  if (css.color && /^#[0-9a-f]{6}$/i.test(css.color)) props['fo:color'] = css.color;
  return props;
}

function cssTextToStyleProps(cssText: string): Record<string, string> {
  if (!cssText.trim()) return {};
  const el = document.createElement('span');
  el.setAttribute('style', cssText);
  return cssToStyleProps(el.style);
}

// Convert a run's inline style string into CSS for the editor.
function inlineCssFromStyle(cssText: string): string {
  const el = document.createElement('span');
  el.setAttribute('style', cssText);
  const parts: string[] = [];
  const style = el.style;
  if (style.fontWeight === 'bold') parts.push('font-weight: bold');
  if (style.fontStyle === 'italic') parts.push('font-style: italic');
  if ((style.textDecoration || '').includes('underline')) parts.push('text-decoration: underline');
  if ((style.textDecoration || '').includes('line-through')) parts.push('text-decoration: line-through');
  if (style.fontSize) parts.push(`font-size: ${style.fontSize}`);
  if (style.fontFamily) parts.push(`font-family: ${style.fontFamily}`);
  if (style.color) parts.push(`color: ${style.color}`);
  return parts.join('; ');
}

export const OdfViewer = forwardRef<OdfViewerHandle, OdfViewerProps>(function OdfViewer(
  { url, workspaceId, path, onDirtyChange },
  ref,
) {
  const editorRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [isEditable, setIsEditable] = useState(true);
  const originalRef = useRef<Document | null>(null);
  const docTypeRef = useRef<'text' | 'spreadsheet' | 'presentation'>('text');
  const dirtyRef = useRef(false);

  const markDirty = useCallback(
    (dirty: boolean) => {
      dirtyRef.current = dirty;
      onDirtyChange?.(dirty);
    },
    [onDirtyChange],
  );

  // ── Serialization helpers ────────────────────────────────────────────────────

  const makeEl = (doc: Document, ns: string, name: string, attrs: Record<string, string> = {}) => {
    const el = doc.createElementNS(ns, name);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  };

  const styleKeyFor = (props: Record<string, string>): string => {
    const keys = ['fo:font-weight', 'style:font-weight-asian', 'fo:font-style', 'style:font-style-asian', 'style:text-underline-style', 'style:text-line-through-style', 'fo:font-size', 'fo:font-family', 'fo:color'];
    return keys.map((k) => `${k}=${props[k] || ''}`).join('|');
  };

  const getOrCreateTextStyle = (doc: Document, cssText: string): string => {
    const autoStyles = doc.getElementsByTagNameNS(NS.office, 'automatic-styles')[0];
    if (!autoStyles) return '';
    const props = cssTextToStyleProps(cssText);
    const key = styleKeyFor(props);
    const existing = (doc as Document).querySelectorAll(`[${localNameOf('style:name')}]`);
    for (let i = 0; i < existing.length; i++) {
      const el = existing[i];
      if (el.getAttribute(localNameOf('style:family')) !== 'text') continue;
      const name = el.getAttribute(localNameOf('style:name')) || '';
      const tp = el.querySelector(`[${localNameOf('style:text-properties')}]`);
      if (!tp) continue;
      const tpProps: Record<string, string> = {};
      const attrs = (tp as Element).attributes;
      for (let j = 0; j < attrs.length; j++) tpProps[attrs[j].name] = attrs[j].value;
      if (styleKeyFor(tpProps) === key) return name;
    }
    let idx = 1;
    let name = 'W1';
    while (autoStyles.querySelector(`[${localNameOf('style:name')}="${name}"]`)) {
      idx += 1;
      name = `W${idx}`;
    }
    const styleEl = makeEl(doc, NS.style, 'style', { [localNameOf('style:name')]: name, [localNameOf('style:family')]: 'text' });
    const tp = makeEl(doc, NS.style, 'text-properties', props);
    styleEl.appendChild(tp);
    autoStyles.appendChild(styleEl);
    return name;
  };

  // Serialize the edited editor DOM back into the body of a fresh content.xml.
  const serializeToXml = (): string => {
    const original = originalRef.current;
    if (!original || !editorRef.current) return '';
    const out = original.cloneNode(true) as Document;
    const bodyText = out.getElementsByTagNameNS(NS.office, 'text')[0];
    if (!bodyText) return '';
    while (bodyText.firstChild) bodyText.removeChild(bodyText.firstChild);

    const appendRuns = (parent: Element, node: Node) => {
      const children = Array.from(node.childNodes);
      for (const child of children) {
        if (child.nodeType === Node.TEXT_NODE) {
          parent.appendChild(out.createTextNode(child.textContent || ''));
        } else if (child.nodeType === Node.ELEMENT_NODE) {
          const el = child as HTMLElement;
          const tag = el.tagName.toLowerCase();
          if (tag === 'br') {
            parent.appendChild(makeEl(out, NS.text, 'line-break'));
          } else if (el.dataset.odfImg) {
            const parser = new DOMParser();
            const imgDoc = parser.parseFromString(el.dataset.odfImg, 'text/xml');
            if (imgDoc.documentElement) parent.appendChild(out.importNode(imgDoc.documentElement, true));
          } else if (tag === 'b' || tag === 'strong') {
            const styleName = getOrCreateTextStyle(out, 'font-weight: bold');
            const span = makeEl(out, NS.text, 'span', styleName ? { [localNameOf('style:name')]: styleName } : {});
            appendRuns(span, el);
            parent.appendChild(span);
          } else if (tag === 'i' || tag === 'em') {
            const styleName = getOrCreateTextStyle(out, 'font-style: italic');
            const span = makeEl(out, NS.text, 'span', styleName ? { [localNameOf('style:name')]: styleName } : {});
            appendRuns(span, el);
            parent.appendChild(span);
          } else if (tag === 'u') {
            const styleName = getOrCreateTextStyle(out, 'text-decoration: underline');
            const span = makeEl(out, NS.text, 'span', styleName ? { [localNameOf('style:name')]: styleName } : {});
            appendRuns(span, el);
            parent.appendChild(span);
          } else if (tag === 's' || tag === 'strike' || tag === 'del') {
            const styleName = getOrCreateTextStyle(out, 'text-decoration: line-through');
            const span = makeEl(out, NS.text, 'span', styleName ? { [localNameOf('style:name')]: styleName } : {});
            appendRuns(span, el);
            parent.appendChild(span);
          } else if (tag === 'span' && el.style && el.style.cssText) {
            const styleName = getOrCreateTextStyle(out, el.style.cssText);
            const span = makeEl(out, NS.text, 'span', styleName ? { [localNameOf('style:name')]: styleName } : {});
            appendRuns(span, el);
            parent.appendChild(span);
          } else {
            appendRuns(parent, el);
          }
        }
      }
    };

    const appendList = (parent: Element, listEl: Element, ordered: boolean) => {
      const list = makeEl(out, NS.text, 'list', ordered ? { [localNameOf('style:name')]: 'L1' } : {});
      Array.from(listEl.childNodes).forEach((li) => {
        if (li.nodeType !== Node.ELEMENT_NODE || (li as HTMLElement).tagName.toLowerCase() !== 'li') return;
        const item = makeEl(out, NS.text, 'list-item');
        const p = makeEl(out, NS.text, 'p');
        appendRuns(p, li);
        item.appendChild(p);
        list.appendChild(item);
      });
      parent.appendChild(list);
    };

    Array.from(editorRef.current.childNodes).forEach((node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        const text = (node.textContent || '').trim();
        if (!text) return;
        const p = makeEl(out, NS.text, 'p');
        p.appendChild(out.createTextNode(text));
        bodyText.appendChild(p);
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      const el = node as HTMLElement;
      const tag = el.tagName.toLowerCase();
      if (tag === 'ul' || tag === 'ol') {
        appendList(bodyText, el, tag === 'ol');
        return;
      }
      const headingMatch = tag.match(/^h([1-6])$/);
      if (headingMatch) {
        const h = makeEl(out, NS.text, 'h', { [localNameOf('outline-level')]: headingMatch[1] });
        appendRuns(h, el);
        bodyText.appendChild(h);
        return;
      }
      const p = makeEl(out, NS.text, 'p');
      appendRuns(p, el);
      bodyText.appendChild(p);
    });

    return new XMLSerializer().serializeToString(out);
  };

  const save = useCallback(async (): Promise<void> => {
    if (!dirtyRef.current || saving) return;
    setSaving(true);
    try {
      const xml = serializeToXml();
      if (!xml) throw new Error('Nothing to save');
      const zip = new JSZip();
      // Re-pack the archive: reuse every original entry, replace content.xml.
      const sourceBlob = await fetch(url).then((r) => r.blob());
      const srcZip = await JSZip.loadAsync(sourceBlob);
      const entries = Object.keys(srcZip.files).filter((k) => !srcZip.files[k].dir);
      for (const name of entries) {
        if (name === 'content.xml') {
          zip.file('content.xml', xml);
        } else {
          zip.file(name, await srcZip.files[name].async('uint8array'));
        }
      }
      const outBlob = await zip.generateAsync({ type: 'blob', mimeType: 'application/vnd.oasis.opendocument.text', compression: 'DEFLATE' });
      const buf = await outBlob.arrayBuffer();
      const bytes = new Uint8Array(buf);
      let binary = '';
      const CHUNK = 0x8000;
      for (let i = 0; i < bytes.length; i += CHUNK) binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
      const b64 = btoa(binary);
      await api.writeWorkspaceFileBase64(workspaceId, path, b64);
      markDirty(false);
      toast.success(`Saved ${path.split('/').pop()}`);
    } catch (e: unknown) {
      toast.error(`Save failed: ${(e as Error)?.message || 'Unknown error'}`);
    } finally {
      setSaving(false);
    }
  }, [saving, url, workspaceId, path, markDirty, serializeToXml]);

  useImperativeHandle(ref, () => ({ save }), [save]);

  // ── Parsing: ODF zip → editable HTML ────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const blob = await fetch(url).then((r) => r.blob());
        if (cancelled) return;
        const zip = await JSZip.loadAsync(blob);
        const contentEntry = zip.file('content.xml');
        if (!contentEntry) {
          setError('Not a valid ODF document (no content.xml)');
          setLoading(false);
          return;
        }
        const xml = await contentEntry.async('string');
        const doc = new DOMParser().parseFromString(xml, 'text/xml');
        originalRef.current = doc;

        const editor = editorRef.current;
        if (!editor) return;
        editor.innerHTML = '';

        const isSpreadsheet = !!doc.getElementsByTagNameNS(NS.office, 'spreadsheet')[0];
        const isPresentation = !!doc.getElementsByTagNameNS(NS.office, 'presentation')[0];
        docTypeRef.current = isSpreadsheet ? 'spreadsheet' : isPresentation ? 'presentation' : 'text';
        setIsEditable(!isSpreadsheet && !isPresentation);

        const appendRuns = (parent: HTMLElement, node: Element, pstyleName: string | null) => {
          Array.from(node.childNodes).forEach((child) => {
            if (child.nodeType === Node.TEXT_NODE) {
              parent.appendChild(document.createTextNode(child.textContent || ''));
              return;
            }
            if (child.nodeType !== Node.ELEMENT_NODE) return;
            const el = child as Element;
            const local = el.localName;
            if (local === 's') {
              const count = Math.max(1, parseInt(el.getAttribute('c') || '1', 10) || 1);
              parent.appendChild(document.createTextNode(' '.repeat(count)));
            } else if (local === 'tab') {
              parent.appendChild(document.createTextNode('\t'));
            } else if (local === 'line-break') {
              parent.appendChild(document.createElement('br'));
            } else if (local === 'span') {
              const props = resolveStyleProps(doc, el.getAttribute(localNameOf('style:name')) || null);
              const css = styleToCss(props);
              const span = document.createElement('span');
              if (Object.keys(css).length) span.setAttribute('style', inlineCssFromStyle(cssTextFromRecord(css)));
              appendRuns(span, el, pstyleName);
              parent.appendChild(span);
            } else if (local === 'frame') {
              // Preserve images: placeholder span carrying the raw XML.
              const raw = new XMLSerializer().serializeToString(el);
              const marker = document.createElement('span');
              marker.dataset.odfImg = raw;
              marker.className = 'inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-indigo-950/40 border border-indigo-500/30 text-indigo-300 text-xs my-1';
              marker.textContent = '[ Image ]';
              parent.appendChild(marker);
            } else if (local === 'note') {
              parent.appendChild(document.createTextNode('[note]'));
            } else {
              appendRuns(parent, el, pstyleName);
            }
          });
        };

        const appendParagraph = (el: Element) => {
          const pstyleName = el.getAttribute(localNameOf('style:name')) || null;
          const isHeading = el.localName === 'h';
          const level = isHeading ? Math.max(1, parseInt(el.getAttribute(localNameOf('outline-level')) || '1', 10) || 1) : 0;
          const tag = isHeading ? `h${Math.min(level, 6)}` : 'p';
          const p = document.createElement(tag);
          if (!isHeading) {
            const props = resolveStyleProps(doc, pstyleName);
            const css = styleToCss(props);
            if (css.textDecoration) p.setAttribute('style', `text-decoration: ${css.textDecoration}`);
          }
          appendRuns(p, el, pstyleName);
          editor.appendChild(p);
        };

        const appendList = (el: Element) => {
          const list = document.createElement('ul');
          Array.from(el.childNodes).forEach((liEl) => {
            if (liEl.nodeType !== Node.ELEMENT_NODE || (liEl as Element).localName !== 'list-item') return;
            const li = document.createElement('li');
            Array.from(liEl.childNodes).forEach((inner) => {
              if (inner.nodeType !== Node.ELEMENT_NODE) return;
              const innerEl = inner as Element;
              if (innerEl.localName === 'p' || innerEl.localName === 'h') {
                const p = document.createElement('p');
                const pstyleName = innerEl.getAttribute(localNameOf('style:name')) || null;
                appendRuns(p, innerEl, pstyleName);
                li.appendChild(p);
              }
            });
            list.appendChild(li);
          });
          editor.appendChild(list);
        };

        if (docTypeRef.current === 'text') {
          const bodyText = doc.getElementsByTagNameNS(NS.office, 'text')[0];
          if (!bodyText) {
            setError('ODF document has no body text');
            setLoading(false);
            return;
          }
          Array.from(bodyText.childNodes).forEach((node) => {
            if (node.nodeType !== Node.ELEMENT_NODE) return;
            const el = node as Element;
            if (el.localName === 'p' || el.localName === 'h') appendParagraph(el);
            else if (el.localName === 'list') appendList(el);
          });
        } else if (docTypeRef.current === 'spreadsheet') {
          const tables = doc.getElementsByTagNameNS(NS.table, 'table');
          if (!tables.length) {
            setError('Spreadsheet has no tables');
            setLoading(false);
            return;
          }
          Array.from(tables).forEach((table, ti) => {
            const wrap = document.createElement('div');
            wrap.className = 'mb-4';
            const caption = document.createElement('p');
            caption.className = 'text-[10px] uppercase tracking-wider text-slate-500 mb-1';
            caption.textContent = `Sheet ${ti + 1}`;
            wrap.appendChild(caption);
            const tbl = document.createElement('table');
            tbl.className = 'w-full text-xs border-collapse';
            Array.from(table.getElementsByTagNameNS(NS.table, 'table-row')).forEach((row) => {
              const tr = document.createElement('tr');
              Array.from(row.getElementsByTagNameNS(NS.table, 'table-cell')).forEach((cell) => {
                const td = document.createElement('td');
                td.className = 'border border-slate-700/60 px-2 py-1 align-top';
                const p = cell.getElementsByTagNameNS(NS.text, 'p');
                if (p.length) {
                  const pstyleName = p[0].getAttribute(localNameOf('style:name')) || null;
                  appendRuns(td, p[0], pstyleName);
                } else {
                  td.textContent = (cell.getAttribute(localNameOf('string-value')) || '').trim();
                }
                tr.appendChild(td);
              });
              tbl.appendChild(tr);
            });
            wrap.appendChild(tbl);
            editor.appendChild(wrap);
          });
        } else {
          const pages = doc.getElementsByTagNameNS(NS.draw, 'page');
          Array.from(pages).forEach((page, pi) => {
            const wrap = document.createElement('div');
            wrap.className = 'mb-5';
            const caption = document.createElement('p');
            caption.className = 'text-[10px] uppercase tracking-wider text-slate-500 mb-1';
            caption.textContent = `Slide ${pi + 1}${page.getAttribute(localNameOf('name')) ? ` — ${page.getAttribute(localNameOf('name'))}` : ''}`;
            wrap.appendChild(caption);
            Array.from(page.getElementsByTagNameNS(NS.text, 'p')).forEach((pEl) => {
              const p = document.createElement('p');
              p.className = 'mb-1';
              const pstyleName = pEl.getAttribute(localNameOf('style:name')) || null;
              appendRuns(p, pEl, pstyleName);
              wrap.appendChild(p);
            });
            editor.appendChild(wrap);
          });
        }
      } catch (e: unknown) {
        setError(`Failed to parse ODF file: ${(e as Error)?.message || 'Unknown error'}`);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [url]);

  // ── Toolbar commands ─────────────────────────────────────────────────────────

  const exec = (command: string, value?: string) => {
    editorRef.current?.focus();
    document.execCommand(command, false, value);
    markDirty(true);
  };

  if (loading) {
    return (
      <div className="h-full flex flex-col">
        <div className="flex-1 min-h-0 overflow-auto bg-white p-8">
          <div
            ref={editorRef}
            contentEditable={isEditable}
            suppressContentEditableWarning
            spellCheck={false}
            onInput={() => markDirty(true)}
            className="mx-auto max-w-3xl bg-white text-slate-900 text-sm leading-relaxed outline-none min-h-full shadow-lg rounded-sm px-10 py-8 prose-headings:font-semibold"
            style={{ fontFamily: 'Georgia, serif' }}
          />
        </div>
        <div className="absolute inset-0 flex items-center justify-center text-sm text-slate-400 bg-[#0c1120]/60">
          <Loader2 size={16} className="animate-spin mr-2" /> Loading document…
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-full flex items-center justify-center p-6">
        <div className="text-sm text-red-400 bg-red-950/30 border border-red-500/30 rounded-xl px-4 py-3 max-w-lg">{error}</div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      {isEditable ? (
        <WordProcessorToolbar dirty={dirtyRef.current} saving={saving} onExec={exec} onSave={() => void save()} />
      ) : (
        <div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-white/10 bg-[#0c1120]">
          <FileType2 size={14} className="text-indigo-400" />
          <span className="text-xs text-slate-400">
            {docTypeRef.current === 'spreadsheet' ? 'Spreadsheet — read-only preview' : 'Presentation — read-only preview'}
          </span>
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-auto bg-white p-8">
        <div
          ref={editorRef}
          contentEditable={isEditable}
          suppressContentEditableWarning
          spellCheck={false}
          onInput={() => markDirty(true)}
          className="mx-auto max-w-3xl bg-white text-slate-900 text-sm leading-relaxed outline-none min-h-full shadow-lg rounded-sm px-10 py-8 prose-headings:font-semibold"
          style={{ fontFamily: 'Georgia, serif' }}
        />
      </div>
    </div>
  );
});

function cssTextFromRecord(css: Record<string, string>): string {
  return Object.entries(css)
    .map(([k, v]) => `${k}: ${v}`)
    .join('; ');
}
