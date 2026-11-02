/**
 * Scaling a recipe's ingredient amounts to a different number of servings.
 *
 * Only the amount an ingredient line starts with is scaled ("1 1/2 cups
 * flour", "2-3 cloves garlic", "½ tsp salt"); a line with no leading amount
 * ("salt to taste") is left as written. Amounts come back as the fractions a
 * cook measures with (⅓, ½, ¾) rather than decimals.
 */

const UNICODE_FRACTIONS: Record<string, number> = {
  '½': 1 / 2, '⅓': 1 / 3, '⅔': 2 / 3, '¼': 1 / 4, '¾': 3 / 4, '⅕': 1 / 5,
  '⅛': 1 / 8, '⅜': 3 / 8, '⅝': 5 / 8, '⅞': 7 / 8,
};
const UNI = Object.keys(UNICODE_FRACTIONS).join('');
const NUMBER = `(?:\\d+\\s+\\d+\\/\\d+|\\d+\\/\\d+|\\d+(?:\\.\\d+)?(?:\\s*[${UNI}])?|[${UNI}])`;
const LEADING = new RegExp(`^(\\s*)(${NUMBER})(?:(\\s*(?:-|\u2013|to)\\s*)(${NUMBER}))?(?=\\s|$|[a-zA-Z(])`);

/** The value of one written amount: "1 1/2", "3/4", "2.5", "1½", "½". */
export function parseAmount(text: string): number | null {
  const t = text.trim();
  let m = /^(\d+)\s+(\d+)\/(\d+)$/.exec(t);
  if (m) return Number(m[1]) + Number(m[2]) / Number(m[3]);
  m = /^(\d+)\/(\d+)$/.exec(t);
  if (m) return Number(m[2]) ? Number(m[1]) / Number(m[2]) : null;
  m = new RegExp(`^(\\d+(?:\\.\\d+)?)?\\s*([${UNI}])?$`).exec(t);
  if (m && (m[1] || m[2])) return (m[1] ? Number(m[1]) : 0) + (m[2] ? UNICODE_FRACTIONS[m[2]] : 0);
  return null;
}

const NICE: [number, string][] = [
  [0, ''], [1 / 8, '⅛'], [1 / 4, '¼'], [1 / 3, '⅓'], [3 / 8, '⅜'], [1 / 2, '½'],
  [5 / 8, '⅝'], [2 / 3, '⅔'], [3 / 4, '¾'], [7 / 8, '⅞'], [1, ''],
];

/** An amount as a cook writes it: "1½", "⅓", "12". */
export function formatAmount(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0';
  if (value >= 20) return String(Math.round(value));
  let whole = Math.floor(value);
  const rest = value - whole;
  let best = NICE[0];
  for (const candidate of NICE) if (Math.abs(candidate[0] - rest) < Math.abs(best[0] - rest)) best = candidate;
  if (best[0] === 1) {
    whole += 1;
    best = NICE[0];
  }
  if (whole === 0 && !best[1]) return '⅛';
  return `${whole || ''}${best[1]}`;
}

/** One ingredient line with its leading amount (or range) scaled by `factor`. */
export function scaleIngredient(line: string, factor: number): string {
  if (factor === 1) return line;
  const m = LEADING.exec(line);
  if (!m) return line;
  const first = parseAmount(m[2]);
  if (first === null) return line;
  const second = m[4] ? parseAmount(m[4]) : null;
  const scaled = formatAmount(first * factor) + (second !== null ? `${m[3]}${formatAmount(second * factor)}` : '');
  return `${m[1]}${scaled}${line.slice(m[0].length)}`;
}
