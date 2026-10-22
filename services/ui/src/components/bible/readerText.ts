/**
 * Imported books arrive as extracted PDF/EPUB text: hard-wrapped lines, blank
 * lines between paragraphs, and a leading page number. On a phone those hard
 * wraps become ragged short lines, so the reader joins them back into real
 * paragraphs before rendering.
 */
export interface BookPage {
  heading: string | null;
  paragraphs: string[];
}

function joinLines(block: string): string {
  return block
    // A PDF breaks hyphenated words across lines. Only rejoin when the next
    // line starts with a lower-case letter, so a dash used as punctuation is
    // left alone.
    .replace(/([A-Za-z])-\n([a-z])/g, '$1$2')
    .replace(/\n+/g, ' ')
    .replace(/[ \t]+/g, ' ')
    .trim();
}

function isHeading(value: string): boolean {
  if (!value || value.length > 90) return false;
  if (/[.!?,;]$/.test(value)) return false;
  return value.split(/\s+/).length <= 14;
}

export function cleanBookPage(raw: string): BookPage {
  const normalized = raw.replace(/\r\n?/g, '\n').trim();
  // The extractor puts the printed page number on its own first line. The
  // reader already shows the page label, so keeping it would print it twice.
  const withoutNumber = normalized.replace(/^\d{1,4}\s*\n+/, '');
  const paragraphs = withoutNumber
    .split(/\n\s*\n+/)
    .map(joinLines)
    .filter(Boolean);

  if (paragraphs.length === 0) return { heading: null, paragraphs: [] };

  const [first, ...rest] = paragraphs;
  if (rest.length > 0 && isHeading(first)) {
    return { heading: first, paragraphs: rest };
  }
  return { heading: null, paragraphs };
}
