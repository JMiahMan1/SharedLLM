import { useEffect, useState } from 'react';
import ExcelJS from 'exceljs';

interface ExcelViewerProps {
  url: string;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ',') {
      row.push(field);
      field = '';
    } else if (c === '\n') {
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else if (c === '\r') {
      // skip CR
    } else {
      field += c;
    }
  }
  if (field !== '' || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  // drop a single trailing empty row
  if (rows.length > 1 && rows[rows.length - 1].length === 1 && rows[rows.length - 1][0] === '') {
    rows.pop();
  }
  return rows;
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
        const bytes = new Uint8Array(buf);
        let out: { name: string; html: string }[] = [];
        if (bytes.length >= 2 && bytes[0] === 0x50 && bytes[1] === 0x4b) {
          const workbook = new ExcelJS.Workbook();
          await workbook.xlsx.load(buf);
          out = workbook.worksheets.map((ws) => {
            const rows: string[] = [];
            ws.eachRow((row) => {
              const cells = (row.values as unknown[]) ?? [];
              const tds = cells
                .slice(1)
                .map((c) => {
                  const v =
                    c && typeof c === 'object' && 'text' in (c as Record<string, unknown>)
                      ? (c as { text: unknown }).text
                      : c && typeof c === 'object' && 'result' in (c as Record<string, unknown>)
                      ? (c as { result: unknown }).result
                      : c;
                  return `<td>${escapeHtml(String(v ?? ''))}</td>`;
                })
                .join('');
              rows.push(`<tr>${tds}</tr>`);
            });
            const html = `<table class="excel-table"><tbody>${rows.join('')}</tbody></table>`;
            return { name: ws.name, html };
          });
        } else if (bytes.length >= 4 && bytes[0] === 0xd0 && bytes[1] === 0xcf && bytes[2] === 0x11 && bytes[3] === 0xe0) {
          throw new Error('Legacy .xls (binary) format is not supported; please convert to .xlsx');
        } else {
          const text = new TextDecoder().decode(buf);
          const rows = parseCsv(text);
          const html = `<table class="excel-table"><tbody>${rows
            .map((r) => `<tr>${r.map((c) => `<td>${escapeHtml(c)}</td>`).join('')}</tr>`)
            .join('')}</tbody></table>`;
          out = [{ name: 'CSV', html }];
        }
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
