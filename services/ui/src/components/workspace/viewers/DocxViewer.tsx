import { useEffect, useRef, useState } from 'react';
import { renderAsync } from 'docx-preview';

interface DocxViewerProps {
  url: string;
}

// Renders .docx files using docx-preview.
export function DocxViewer({ url }: DocxViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const blob = await fetch(url).then((r) => r.blob());
        if (cancelled) return;
        const el = containerRef.current;
        if (!el) return;
        el.innerHTML = '';
        await renderAsync(blob, el, undefined, {
          className: 'docx-wrapper',
          inWrapper: true,
          ignoreWidth: false,
          ignoreHeight: false,
          breakPages: true,
        });
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to render Word document');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [url]);

  return (
    <div className="h-full w-full overflow-auto custom-scrollbar bg-white p-4">
      {loading && <div className="text-slate-400 text-sm p-4">Loading document…</div>}
      {error && <div className="text-red-500 text-sm p-4">Word error: {error}</div>}
      <div ref={containerRef} className="mx-auto max-w-3xl bg-white shadow" />
    </div>
  );
}
