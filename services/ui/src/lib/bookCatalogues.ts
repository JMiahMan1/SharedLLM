import type { BookCatalogueId, BookCatalogueReport, BookCatalogues, BookCatalogueSource } from '../types/api';

/** Plain helpers the book catalogue tile and the library page share (kept out of component files for fast refresh). */

export const CATALOGUE_NAMES: Record<BookCatalogueId, string> = {
  google_books: 'Google Books',
  open_library: 'Open Library',
};

/** The one line a catalogue card shows, and whether it is a problem. */
export function catalogueLine(id: BookCatalogueId, source: BookCatalogueSource | undefined): { text: string; problem: boolean } {
  if (!source) return { text: 'Not set up', problem: false };
  if (source.ready) {
    if (id === 'google_books') {
      return { text: source.key_source === 'household' ? 'On, with the household key' : 'On, with your own key', problem: false };
    }
    return { text: 'On', problem: false };
  }
  if (source.enabled) return { text: 'On, but no API key is set', problem: true };
  if (id === 'google_books' && !source.key_set) return { text: 'Not set up', problem: false };
  return { text: 'Off', problem: false };
}

/** Which catalogues fill in books for this person, and why any is not used (GET /api/library/catalogues). */
export function cataloguesSummary(catalogues: BookCatalogues): { using: string[]; notUsed: string[]; problems: string[] } {
  const using: string[] = [];
  const notUsed: string[] = [];
  const problems: string[] = [];
  for (const id of catalogues.order) {
    const source = catalogues.sources[id];
    if (!source) continue;
    if (source.ready) using.push(source.name);
    else notUsed.push(source.reason || `${source.name}: not set up`);
    if (source.ready && source.last && !source.last.ok && source.last.error) problems.push(`Last lookup: ${source.last.error}`);
  }
  return { using, notUsed, problems };
}

/** One lookup's catalogues, said plainly: asked, skipped and why, errors. */
export function catalogueReportLines(report: BookCatalogueReport | undefined): { text: string; problem: boolean }[] {
  if (!report) return [];
  return [
    ...(report.asked.length ? [{ text: `Asked ${report.asked.join(' and ')}.`, problem: false }] : []),
    ...report.errors.map((e) => ({ text: e.error, problem: true })),
    ...report.skipped.map((s) => ({ text: `Skipped ${s.reason.startsWith(s.source) ? s.reason : `${s.source}: ${s.reason}`}.`, problem: false })),
  ];
}
