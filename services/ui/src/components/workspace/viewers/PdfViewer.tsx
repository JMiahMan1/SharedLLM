import { useEffect, useRef, useState } from 'react';
import * as pdfjs from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

interface PdfViewerProps {
  url: string;
}

// Renders a PDF to stacked canvases using pdf.js.
export function PdfViewer({ url }: PdfViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [pages, setPages] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const doc = await pdfjs.getDocument(url).promise;
        if (cancelled) return;
        setPages(doc.numPages);
        const container = containerRef.current;
        if (!container) return;
        container.innerHTML = '';
        const scale = Math.min(2, Math.max(1, (container.clientWidth || 800) / 800));
        for (let i = 1; i <= doc.numPages; i++) {
          const page = await doc.getPage(i);
          const viewport = page.getViewport({ scale });
          const canvas = document.createElement('canvas');
          canvas.className = 'mx-auto my-2 block bg-white shadow-lg';
          const ctx = canvas.getContext('2d')!;
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.style.width = `${viewport.width}px`;
          canvas.style.height = `${viewport.height}px`;
          container.appendChild(canvas);
          await page.render({ canvasContext: ctx, viewport }).promise;
          page.cleanup();
        }
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to render PDF');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [url]);

  return (
    <div className="h-full w-full overflow-auto custom-scrollbar bg-[#1a1f2e] p-3">
      {loading && <div className="text-slate-400 text-sm p-4">Loading PDF…</div>}
      {error && <div className="text-red-400 text-sm p-4">PDF error: {error}</div>}
      {pages > 0 && <div className="text-[11px] text-slate-500 mb-2">{pages} page(s)</div>}
      <div ref={containerRef} className="flex flex-col items-center" />
    </div>
  );
}
