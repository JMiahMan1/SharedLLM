import { useCallback, useEffect, useImperativeHandle, useRef, useState, forwardRef } from 'react';
import JSZip from 'jszip';
import { Loader2, FileType2 } from 'lucide-react';
import { api } from '../../../services/api';
import toast from 'react-hot-toast';
import { WordProcessorToolbar } from './WordProcessorToolbar';

// DOCX is a ZIP whose body lives in word/document.xml. We render its
// paragraphs into a contentEditable surface with the shared word-processor
// toolbar, then serialize the edited DOM back into document.xml and re-pack
// the archive (preserving every other part untouched).

const W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';

interface DocxEditorHandle {
  save: () => Promise<void>;
}

interface DocxEditorProps {
  url: string;
  workspaceId: string;
  path: string;
  onDirtyChange?: (dirty: boolean) => void;
}

function qn(local: string): string {
  return `w:${local}`;
}

// XML DOMs (DOMParser 'text/xml') do not support CSS selectors on
// namespace-prefixed element names at all, so find descendants by
// localName with a tree walk instead.
function qsel(root: Element | null, local: string): Element | null {
  if (!root) return null;
  const walker = (root.ownerDocument || document).createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
  while (walker.nextNode()) {
    const node = walker.currentNode as Element;
    if (node !== root && node.localName === local) return node;
  }
  return null;
}

function qselDirectChildren(root: Element, local: string): Element[] {
  return Array.from(root.childNodes).filter(
    (n): n is Element => n.nodeType === Node.ELEMENT_NODE && (n as Element).localName === local,
  );
}

// Resolve effective run properties: local rPr merged over paragraph rPr and
// the docDefaults / named styles hierarchy.
function resolveRpr(doc: Document, rPr: Element | null, pPr: Element | null): Record<string, string> {
  const out: Record<string, string> = {};

  const styleId = qsel(pPr, 'pStyle')?.getAttribute('w:val') || null;
  if (styleId) {
    const styleEl = Array.from(doc.getElementsByTagNameNS(W, 'style')).find(
      (s) => s.getAttribute('w:styleId') === styleId && (s.getAttribute('w:type') === 'paragraph' || s.getAttribute('w:type') === 'character'),
    );
    if (styleEl) {
      const srPr = qsel(styleEl, 'rPr');
      if (srPr) applyRprElement(srPr, out);
      const basedOn = qsel(styleEl, 'basedOn')?.getAttribute('w:val');
      if (basedOn) {
        const baseEl = Array.from(doc.getElementsByTagNameNS(W, 'style')).find((s) => s.getAttribute('w:styleId') === basedOn);
        if (baseEl) {
          const brPr = qsel(baseEl, 'rPr');
          if (brPr) applyRprElement(brPr, out);
        }
      }
    }
  }

  const defaults = doc.getElementsByTagNameNS(W, 'docDefaults')[0];
  const drPr = qsel(qsel(defaults, 'rPrDefault'), 'rPr');
  if (drPr) applyRprElement(drPr, out);

  if (pPr) {
    const prPr = qsel(pPr, 'rPr');
    if (prPr) applyRprElement(prPr, out);
  }
  if (rPr) applyRprElement(rPr, out);
  return out;
}

function applyRprElement(rPr: Element, out: Record<string, string>) {
  const b = qsel(rPr, 'b');
  if (b && b.getAttribute('w:val') !== '0' && b.getAttribute('w:val') !== 'false') out.bold = '1';
  const i = qsel(rPr, 'i');
  if (i && i.getAttribute('w:val') !== '0' && i.getAttribute('w:val') !== 'false') out.italic = '1';
  const u = qsel(rPr, 'u');
  if (u && u.getAttribute('w:val') && u.getAttribute('w:val') !== 'none') out.underline = '1';
  const strike = qsel(rPr, 'strike');
  if (strike && strike.getAttribute('w:val') !== '0' && strike.getAttribute('w:val') !== 'false') out.strike = '1';
  const sz = qsel(rPr, 'sz');
  if (sz?.getAttribute('w:val')) out.size = sz.getAttribute('w:val') || '';
  const color = qsel(rPr, 'color');
  if (color?.getAttribute('w:val')) out.color = color.getAttribute('w:val') || '';
  const fonts = qsel(rPr, 'rFonts');
  if (fonts?.getAttribute('w:ascii')) out.font = fonts.getAttribute('w:ascii') || '';
}

function rprToCss(props: Record<string, string>): string {
  const parts: string[] = [];
  if (props.bold) parts.push('font-weight: bold');
  if (props.italic) parts.push('font-style: italic');
  const dec: string[] = [];
  if (props.underline) dec.push('underline');
  if (props.strike) dec.push('line-through');
  if (dec.length) parts.push(`text-decoration: ${dec.join(' ')}`);
  if (props.size) parts.push(`font-size: ${Math.round(parseInt(props.size, 10) / 2)}pt`);
  if (props.color) parts.push(`color: #${props.color}`);
  if (props.font) parts.push(`font-family: ${props.font}`);
  return parts.join('; ');
}

function cssToRpr(doc: Document, cssText: string): Element {
  const rPr = doc.createElementNS(W, qn('rPr'));
  const probe = document.createElement('span');
  probe.setAttribute('style', cssText);
  const st = probe.style;
  if (st.fontWeight === 'bold') {
    const b = doc.createElementNS(W, qn('b'));
    b.setAttribute('w:val', '1');
    rPr.appendChild(b);
  }
  if (st.fontStyle === 'italic') {
    const i = doc.createElementNS(W, qn('i'));
    i.setAttribute('w:val', '1');
    rPr.appendChild(i);
  }
  const dec = st.textDecoration || '';
  if (dec.includes('underline')) {
    const u = doc.createElementNS(W, qn('u'));
    u.setAttribute('w:val', 'single');
    rPr.appendChild(u);
  }
  if (dec.includes('line-through')) {
    const s = doc.createElementNS(W, qn('strike'));
    s.setAttribute('w:val', '1');
    rPr.appendChild(s);
  }
  const m = (st.fontSize || '').match(/([\d.]+)(pt|px)/);
  if (m) {
    const pt = m[2] === 'pt' ? parseFloat(m[1]) : parseFloat(m[1]) * 0.75;
    const sz = doc.createElementNS(W, qn('sz'));
    sz.setAttribute('w:val', String(Math.round(pt * 2)));
    rPr.appendChild(sz);
    const szCs = doc.createElementNS(W, qn('szCs'));
    szCs.setAttribute('w:val', String(Math.round(pt * 2)));
    rPr.appendChild(szCs);
  }
  if (st.color && /^#[0-9a-f]{6}$/i.test(st.color)) {
    const c = doc.createElementNS(W, qn('color'));
    c.setAttribute('w:val', st.color.slice(1));
    rPr.appendChild(c);
  }
  if (st.fontFamily && st.fontFamily !== 'Georgia, serif') {
    const f = doc.createElementNS(W, qn('rFonts'));
    const fam = st.fontFamily.split(',')[0].trim().replace(/['"]/g, '');
    f.setAttribute('w:ascii', fam);
    f.setAttribute('w:hAnsi', fam);
    rPr.appendChild(f);
  }
  return rPr;
}

export const DocxEditor = forwardRef<DocxEditorHandle, DocxEditorProps>(function DocxEditor(
  { url, workspaceId, path, onDirtyChange },
  ref,
) {
  const editorRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const originalRef = useRef<Document | null>(null);
  const parsedHtmlRef = useRef<string | null>(null);

  const markDirty = useCallback(
    (d: boolean) => {
      setDirty(d);
      onDirtyChange?.(d);
    },
    [onDirtyChange],
  );

  const makeW = (doc: Document, local: string, attrs: Record<string, string> = {}) => {
    const el = doc.createElementNS(W, qn(local));
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  };

  // Serialize the editor DOM back into word/document.xml.
  const serializeToXml = (): string => {
    const original = originalRef.current;
    if (!original || !editorRef.current) return '';
    const out = original.cloneNode(true) as Document;
    const body = out.getElementsByTagNameNS(W, 'body')[0];
    if (!body) return '';
    while (body.firstChild) body.removeChild(body.firstChild);

    const appendRuns = (parent: Element, node: Node) => {
      Array.from(node.childNodes).forEach((child) => {
        if (child.nodeType === Node.TEXT_NODE) {
          const text = child.textContent || '';
          if (!text) return;
          const r = makeW(out, 'r');
          const t = makeW(out, 't', text.trim() !== text ? { 'xml:space': 'preserve' } : {});
          t.appendChild(out.createTextNode(text));
          r.appendChild(t);
          parent.appendChild(r);
        } else if (child.nodeType === Node.ELEMENT_NODE) {
          const el = child as HTMLElement;
          const tag = el.tagName.toLowerCase();
          if (tag === 'br') {
            const r = makeW(out, 'r');
            r.appendChild(makeW(out, 'br'));
            parent.appendChild(r);
          } else if (tag === 'span' && el.style.cssText) {
            const r = makeW(out, 'r');
            const rPr = cssToRpr(out, el.style.cssText);
            if (rPr.childNodes.length) r.appendChild(rPr);
            const t = makeW(out, 't', (el.textContent || '').trim() !== (el.textContent || '') ? { 'xml:space': 'preserve' } : {});
            t.appendChild(out.createTextNode(el.textContent || ''));
            r.appendChild(t);
            parent.appendChild(r);
          } else if (tag === 'b' || tag === 'strong') {
            const r = makeW(out, 'r');
            const rPr = cssToRpr(out, 'font-weight: bold');
            if (rPr.childNodes.length) r.appendChild(rPr);
            const t = makeW(out, 't', (el.textContent || '').trim() !== (el.textContent || '') ? { 'xml:space': 'preserve' } : {});
            t.appendChild(out.createTextNode(el.textContent || ''));
            r.appendChild(t);
            parent.appendChild(r);
          } else if (tag === 'i' || tag === 'em') {
            const r = makeW(out, 'r');
            const rPr = cssToRpr(out, 'font-style: italic');
            if (rPr.childNodes.length) r.appendChild(rPr);
            const t = makeW(out, 't', (el.textContent || '').trim() !== (el.textContent || '') ? { 'xml:space': 'preserve' } : {});
            t.appendChild(out.createTextNode(el.textContent || ''));
            r.appendChild(t);
            parent.appendChild(r);
          } else if (tag === 'u') {
            const r = makeW(out, 'r');
            const rPr = cssToRpr(out, 'text-decoration: underline');
            if (rPr.childNodes.length) r.appendChild(rPr);
            const t = makeW(out, 't', (el.textContent || '').trim() !== (el.textContent || '') ? { 'xml:space': 'preserve' } : {});
            t.appendChild(out.createTextNode(el.textContent || ''));
            r.appendChild(t);
            parent.appendChild(r);
          } else if (tag === 's' || tag === 'strike' || tag === 'del') {
            const r = makeW(out, 'r');
            const rPr = cssToRpr(out, 'text-decoration: line-through');
            if (rPr.childNodes.length) r.appendChild(rPr);
            const t = makeW(out, 't', (el.textContent || '').trim() !== (el.textContent || '') ? { 'xml:space': 'preserve' } : {});
            t.appendChild(out.createTextNode(el.textContent || ''));
            r.appendChild(t);
            parent.appendChild(r);
          } else {
            appendRuns(parent, child);
          }
        }
      });
    };

    Array.from(editorRef.current.childNodes).forEach((node) => {
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      const el = node as HTMLElement;
      const tag = el.tagName.toLowerCase();
      if (tag === 'ul' || tag === 'ol') {
        const numId = tag === 'ol' ? '3' : '2';
        Array.from(el.childNodes).forEach((li) => {
          if (li.nodeType !== Node.ELEMENT_NODE || (li as HTMLElement).tagName.toLowerCase() !== 'li') return;
          const p = makeW(out, 'p');
          const pPr = makeW(out, 'pPr');
          const numPr = makeW(out, 'numPr');
          numPr.appendChild(makeW(out, 'ilvl', { 'w:val': '0' }));
          numPr.appendChild(makeW(out, 'numId', { 'w:val': numId }));
          pPr.appendChild(numPr);
          p.appendChild(pPr);
          appendRuns(p, li);
          body.appendChild(p);
        });
        return;
      }
      const headingMatch = tag.match(/^h([1-6])$/);
      if (headingMatch) {
        const p = makeW(out, 'p');
        const pPr = makeW(out, 'pPr');
        pPr.appendChild(makeW(out, 'outlineLvl', { 'w:val': String(Number(headingMatch[1]) - 1) }));
        p.appendChild(pPr);
        appendRuns(p, el);
        body.appendChild(p);
        return;
      }
      const p = makeW(out, 'p');
      appendRuns(p, el);
      body.appendChild(p);
    });

    return new XMLSerializer().serializeToString(out);
  };

  const save = useCallback(async (): Promise<void> => {
    if (!dirty || saving) return;
    setSaving(true);
    try {
      const xml = serializeToXml();
      if (!xml) throw new Error('Nothing to save');
      const zip = new JSZip();
      const sourceBlob = await fetch(url).then((r) => r.blob());
      const srcZip = await JSZip.loadAsync(sourceBlob);
      const entries = Object.keys(srcZip.files).filter((k) => !srcZip.files[k].dir);
      for (const name of entries) {
        if (name === 'word/document.xml') {
          zip.file('word/document.xml', xml);
        } else {
          zip.file(name, await srcZip.files[name].async('uint8array'));
        }
      }
      const outBlob = await zip.generateAsync({
        type: 'blob',
        mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        compression: 'DEFLATE',
      });
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
  }, [dirty, saving, url, workspaceId, path, markDirty, serializeToXml]);

  useImperativeHandle(ref, () => ({ save }), [save]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const blob = await fetch(url).then((r) => r.blob());
        if (cancelled) return;
        const zip = await JSZip.loadAsync(blob);
        const entry = zip.file('word/document.xml');
        if (!entry) {
          setError('Not a valid DOCX (no word/document.xml)');
          setLoading(false);
          return;
        }
        const xml = await entry.async('string');
        const doc = new DOMParser().parseFromString(xml, 'text/xml');
        originalRef.current = doc;

        const holder = document.createElement('div');

        const body = doc.getElementsByTagNameNS(W, 'body')[0];
        const processParagraph = (pEl: Element, appendTo: HTMLElement) => {
          const pPr = qsel(pEl, 'pPr');
          const isHeading = qsel(pPr, 'outlineLvl')?.getAttribute('w:val');
          const align = qsel(pPr, 'jc')?.getAttribute('w:val');
          const p = document.createElement(isHeading !== undefined ? `h${Math.min(Number(isHeading) + 1, 6)}` : 'p');
          if (align) p.setAttribute('style', `text-align: ${align === 'both' ? 'justify' : align}`);
          const runs = qselDirectChildren(pEl, 'r');
          runs.forEach((r) => {
            const rPr = qsel(r, 'rPr');
            const props = resolveRpr(doc, rPr, pPr);
            const css = rprToCss(props);
            const span = document.createElement('span');
            if (css) span.setAttribute('style', css);
            Array.from(r.childNodes).forEach((c) => {
              if (c.nodeType === Node.TEXT_NODE) {
                span.appendChild(document.createTextNode(c.textContent || ''));
              } else if (c.nodeType === Node.ELEMENT_NODE) {
                const cl = c as Element;
                if (cl.localName === 't') span.appendChild(document.createTextNode(cl.textContent || ''));
                else if (cl.localName === 'br') span.appendChild(document.createElement('br'));
                else if (cl.localName === 'tab') span.appendChild(document.createTextNode('\t'));
                else if (cl.localName === 'drawing') {
                  const marker = document.createElement('span');
                  marker.dataset.docxRaw = new XMLSerializer().serializeToString(cl);
                  marker.className = 'inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-indigo-950/40 border border-indigo-500/30 text-indigo-300 text-xs my-1';
                  marker.textContent = '[ Image ]';
                  span.appendChild(marker);
                }
              }
            });
            p.appendChild(span);
          });
          appendTo.appendChild(p);
        };

        Array.from(body.childNodes).forEach((node) => {
          if (node.nodeType !== Node.ELEMENT_NODE) return;
          const el = node as Element;
          if (el.localName === 'p') processParagraph(el, holder);
          else if (el.localName === 'tbl') {
            // Render tables read-only as best-effort HTML.
            const tbl = document.createElement('table');
            tbl.className = 'w-full text-xs border-collapse mb-2';
            Array.from(el.getElementsByTagNameNS(W, 'tr')).forEach((tr) => {
              const trEl = document.createElement('tr');
              Array.from(tr.getElementsByTagNameNS(W, 'tc')).forEach((tc) => {
                const td = document.createElement('td');
                td.className = 'border border-slate-400 px-2 py-1 align-top';
                Array.from(tc.childNodes).forEach((c) => {
                  if (c.nodeType === Node.ELEMENT_NODE && (c as Element).localName === 'p') processParagraph(c as Element, td);
                });
                trEl.appendChild(td);
              });
              tbl.appendChild(trEl);
            });
            holder.appendChild(tbl);
          }
        });
        parsedHtmlRef.current = holder.innerHTML;
      } catch (e: unknown) {
        setError(`Failed to parse DOCX: ${(e as Error)?.message || 'Unknown error'}`);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [url]);

  // Populate the editor once it is mounted (the parse above may complete
  // while the loading state is still showing, when the editor div does not
  // exist yet). Runs after every commit; consumes parsedHtmlRef once.
  useEffect(() => {
    if (!editorRef.current || parsedHtmlRef.current === null) return;
    editorRef.current.innerHTML = parsedHtmlRef.current;
    parsedHtmlRef.current = null;
  });

  const exec = (command: string, value?: string) => {
    editorRef.current?.focus();
    document.execCommand(command, false, value);
    markDirty(true);
  };

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-slate-400">
        <Loader2 size={16} className="animate-spin mr-2" /> Loading document…
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
      <div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-white/10 bg-[#0c1120]">
        <FileType2 size={14} className="text-indigo-400" />
        <span className="text-xs text-slate-400">Word document — editable</span>
      </div>
      <WordProcessorToolbar dirty={dirty} saving={saving} onExec={exec} onSave={() => void save()} />
      <div className="flex-1 min-h-0 overflow-auto bg-white p-8">
        <div
          ref={editorRef}
          contentEditable
          suppressContentEditableWarning
          spellCheck={false}
          onInput={() => markDirty(true)}
          className="mx-auto max-w-3xl bg-white text-slate-900 text-sm leading-relaxed outline-none min-h-full shadow-lg rounded-sm px-10 py-8"
          style={{ fontFamily: 'Georgia, serif' }}
        />
      </div>
    </div>
  );
});
