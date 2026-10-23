/**
 * The shopping list for the week's plan.
 *
 * Recipes are Notes-compatible markdown and their ingredients are checkbox
 * lines. The list is generated from the planned recipes, merging identical
 * items; regenerating keeps whatever was already ticked off (matched by item
 * text) so a mid-week refresh does not lose progress.
 */

export const SHOPPING_TITLE = 'Shopping list';
export const SHOPPING_PATH = 'Recipes/Shopping list.md';

const CHECKBOX = /^-\s*\[([ xX])\]\s*(.+?)\s*$/;
const PLAIN = /^-\s+(?!\[)(.+?)\s*$/;

export interface ShoppingItem {
  text: string;
  checked: boolean;
}

export function normalizeItem(text: string): string {
  return text.trim().toLowerCase().replace(/\s+/g, ' ');
}

/** The ingredient lines of a recipe note (checkbox or plain bullets). */
export function ingredientLines(body: string): string[] {
  const out: string[] = [];
  for (const line of body.split('\n')) {
    const checkbox = CHECKBOX.exec(line);
    if (checkbox) {
      const text = checkbox[2].trim();
      if (text) out.push(text);
      continue;
    }
    const plain = PLAIN.exec(line);
    if (plain) {
      const text = plain[1].trim();
      if (text) out.push(text);
    }
  }
  return out;
}

/** The rows of a shopping list note. */
export function parseShoppingList(body: string): ShoppingItem[] {
  const out: ShoppingItem[] = [];
  for (const line of body.split('\n')) {
    const checkbox = CHECKBOX.exec(line);
    if (checkbox) {
      const text = checkbox[2].trim();
      if (text) out.push({ text, checked: checkbox[1].toLowerCase() === 'x' });
      continue;
    }
    const plain = PLAIN.exec(line);
    if (plain) {
      const text = plain[1].trim();
      if (text) out.push({ text, checked: false });
    }
  }
  return out;
}

/**
 * Build the list: ingredients merged by text, ticks kept by text, and
 * anything already ticked that dropped off the plan stays, still ticked.
 */
export function buildShoppingList(previousBody: string, ingredientTexts: string[]): string {
  const previous = parseShoppingList(previousBody);
  const ticked = new Map<string, boolean>();
  for (const item of previous) ticked.set(normalizeItem(item.text), item.checked);

  const rows: ShoppingItem[] = [];
  const seen = new Set<string>();
  for (const raw of ingredientTexts) {
    const key = normalizeItem(raw);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    rows.push({ text: raw.trim(), checked: ticked.get(key) ?? false });
  }
  for (const item of previous) {
    const key = normalizeItem(item.text);
    if (item.checked && key && !seen.has(key)) {
      seen.add(key);
      rows.push({ text: item.text, checked: true });
    }
  }

  const lines = ['#shopping-list', ''];
  for (const row of rows) lines.push(`- [${row.checked ? 'x' : ' '}] ${row.text}`);
  if (rows.length === 0) lines.push('Nothing to buy yet — plan some dinners first.');
  lines.push('');
  lines.push("Made from the week's plan (`Meal plan`).");
  return lines.join('\n');
}
