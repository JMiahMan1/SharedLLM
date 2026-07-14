import { useEffect, useState } from 'react';
import * as XLSX from 'xlsx';

interface ExcelViewerProps {
  url: string;
}

// Renders .xlsx/.xls/.csv workbooks as HTML tables (one section per sheet).
export function ExcelViewer({ url }: ExcelViewerProps) {
  const [sheets, setSheets] = useState<{ name: string; html: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const buf = await fetch(url).then((r) => r.arrayBuffer());
        if (cancelled) return;
        const wb = XLSX.read(buf, { type: 'array' });
        const out = wb.SheetNames.map((name: string) => {
          const ws = wb.Sheets[name];
          const html = XLSX.utils.sheet_to_html(ws, { editable: false });
          return { name, html };
        });
        if (!cancelled) setSheets(out);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to read spreadsheet');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [url]);

  return (
    <div className="h-full w-full overflow-auto custom-scrollbar bg-white p-4 text-slate-900">
      {loading && <div className="text-slate-400 text-sm p-4">Loading spreadsheet…</div>}
      {error && <div className="text-red-500 text-sm p-4">Spreadsheet error: {error}</div>}
      {sheets.map((s) => (
        <div key={s.name} className="mb-6">
          <div className="text-sm font-semibold mb-2 text-slate-700">Sheet: {s.name}</div>
          <div className="overflow-auto" dangerouslySetInnerHTML={{ __html: s.html }} />
        </div>
      ))}
    </div>
  );
}
