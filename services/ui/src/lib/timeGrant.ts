/** What a parent may give for a request for more time (identity checks the same range). */
export const GRANT_MIN = 5;
export const GRANT_MAX = 240;
/** The quick picks beside the field; the asked amount joins them. */
export const QUICK_PICKS = [15, 30, 45, 60];

/** A typed amount as minutes to give, or null when it is not a whole number in range. */
export function grantMinutes(text: string): number | null {
  if (!/^\d+$/.test(text.trim())) return null;
  const n = Number(text.trim());
  return n >= GRANT_MIN && n <= GRANT_MAX ? n : null;
}
