/**
 * Sharing text for a recipe note.
 *
 * Recipes are stored as Notes-compatible markdown: a `# Title` line, the
 * `Category: Recipes` header lines, then the body. The chat message carries
 * an optional intro note and a short excerpt, never the whole file.
 */

import { withShareNote } from './verseShare';

const EXCERPT_CHARS = 220;
const HEADER_KEYS = ['Category:', 'Tags:', 'Times:', 'Source:'];

/** The recipe text with the stored markdown header removed. */
export function recipeBodyText(body: string): string {
  const lines = body.split('\n');
  const head = lines.slice(0, 4).map((line) => line.trim());
  const storedHeader =
    Boolean(head[0]?.startsWith('# ')) && head.some((line) => line.startsWith('Category:'));
  let start = 0;
  if (storedHeader) {
    while (start < lines.length) {
      const trimmed = lines[start].trim();
      if (trimmed === '' || trimmed.startsWith('# ') || HEADER_KEYS.some((key) => trimmed.startsWith(key))) {
        start += 1;
      } else {
        break;
      }
    }
  }
  while (start < lines.length && !lines[start].trim()) start += 1;
  return lines.slice(start).join('\n').trim();
}

function excerpt(text: string, limit = EXCERPT_CHARS): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  if (flat.length <= limit) return flat;
  const clipped = flat.slice(0, limit);
  const at = clipped.lastIndexOf(' ');
  const cut = at > limit * 0.6 ? clipped.slice(0, at) : clipped;
  return `${cut.trimEnd()}…`;
}

/** A short preview line for the share sheet (header stripped, no long tail). */
export function recipeSharePreview(body: string): string {
  return excerpt(recipeBodyText(body));
}

/** The chat message: an optional note first, then the title with an excerpt. */
export function recipeShareText(title: string, body: string, note?: string): string {
  const excerptText = excerpt(recipeBodyText(body));
  const line = excerptText ? `🍽 ${title}: ${excerptText}` : `🍽 ${title}`;
  return withShareNote(line, note);
}
