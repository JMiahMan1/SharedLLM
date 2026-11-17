import type { ReactNode } from 'react';

/** Results per page; "Show more" asks for the next page. */
export const SEARCH_PAGE = 40;

/** The search text with the screen's filters added in the server's own words. */
export function effectiveQuery(term: string, where: string, every: boolean): string {
  let q = term.trim();
  if (where) q += ` in:${where}`;
  if (every && !/\bver:/i.test(q)) q += ' ver:all';
  return q;
}

/** The verse text with the matched words marked; overlapping spans are merged. */
export function highlighted(text: string, spans: number[][] | undefined): ReactNode[] {
  if (!spans?.length) return [text];
  const merged: number[][] = [];
  for (const [start, end] of [...spans].sort((a, b) => a[0] - b[0])) {
    const last = merged[merged.length - 1];
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  const out: ReactNode[] = [];
  let at = 0;
  merged.forEach(([start, end], i) => {
    if (start > at) out.push(text.slice(at, start));
    out.push(
      <mark key={i} className="rounded bg-amber-400/20 px-0.5 text-amber-100">
        {text.slice(start, end)}
      </mark>,
    );
    at = end;
  });
  if (at < text.length) out.push(text.slice(at));
  return out;
}


const FILTER = /(^|\s)-?[a-z]+:("[^"]*"|\S+)/gi;

/** The search text as a question: the filters (`in:`, `ver:`, `has:`) taken out. */
export function questionText(term: string): string {
  return term.replace(FILTER, ' ').replace(/\s+/g, ' ').trim();
}

/** The first `in:` value of a search, so a question looks in the same place. */
export function scopeIn(term: string): string {
  const match = /(?:^|\s)in:("[^"]*"|\S+)/i.exec(term);
  return match ? match[1].replace(/"/g, '') : '';
}
