/** A book, a chapter and maybe a verse or a range: "John 3:16", "Ps 23", "1 Cor 13:4-7". */
const REFERENCE =
  /^\s*(?:[1-3]\s*)?[a-z][a-z .']*?\s*\d+(?:\s*[:.]\s*\d+)?(?:\s*[-\u2013]\s*\d+(?:\s*[:.]\s*\d+)?)?(?:\s*[,;]\s*[\d:.\s,;\-\u2013]*)?\s*$/i;

/**
 * Is this a passage to open rather than words to search? A digit alone is not
 * enough: "G26", "in:1cor grace" and "faith NEAR/5 works" are searches.
 */
export function looksLikeReference(value: string): boolean {
  if (/\b[a-z]+:/i.test(value.replace(/\d+\s*:\s*\d+/g, ''))) return false;
  if (/["*]|\bNEAR\b|(?:^|\s)-\S|\bOR\b/.test(value)) return false;
  if (/^\s*[GH]\d+\s*$/i.test(value)) return false;
  return REFERENCE.test(value);
}
