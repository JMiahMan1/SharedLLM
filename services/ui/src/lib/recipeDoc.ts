/**
 * A recipe note as fields, and back.
 *
 * A cookbook recipe is plain markdown: `Tags:`, `Times:` and `Source:` lines,
 * then `## Ingredients`, `## Steps`, `## Notes` and `## Nutrition`. The card
 * reads it as fields and the editor writes the fields back in that same
 * shape, so every other reader (shopping list, Health, Skylight, the server's
 * browsing facts) keeps working. Sections it does not know are kept as written.
 * Pictures kept beside the note (the card a recipe was photographed from) are
 * lifted out wherever they sit and written back at the end.
 */

import { ATTACHMENT_LINE } from './noteAttachments';

export interface RecipeDoc {
  tags: string[];
  prepMinutes: number | null;
  cookMinutes: number | null;
  totalMinutes: number | null;
  servings: number | null;
  source: string;
  /** Text before the first section that is not a header line. */
  intro: string;
  ingredients: string[];
  steps: string[];
  notes: string;
  /** The `## Nutrition` section as written (the server's stated values). */
  nutrition: string;
  /** Any other `## Section`, kept verbatim and written back after Notes. */
  extra: string;
  /** Attachment lines (`![card.jpg](.attachments.42/card.jpg)`), as written. */
  attachments: string[];
}

const STEP_HEADINGS = new Set(['steps', 'instructions', 'directions', 'method', 'preparation']);

function minutes(times: string, label: string): number | null {
  const m = new RegExp(`\\b${label}\\s+(\\d+)\\s*min`, 'i').exec(times);
  if (m) return Number(m[1]);
  const h = new RegExp(`\\b${label}\\s+(\\d+)\\s*h(?:ours?|r)?(?:\\s+(\\d+)\\s*min)?`, 'i').exec(times);
  return h ? Number(h[1]) * 60 + Number(h[2] ?? 0) : null;
}

function bullet(line: string): string {
  return line.replace(/^\s*(?:[-*]\s*(?:\[[ xX]\]\s*)?|\d+[.)]\s*)/, '').trim();
}

export function parseRecipeDoc(body: string): RecipeDoc {
  const doc: RecipeDoc = {
    tags: [], prepMinutes: null, cookMinutes: null, totalMinutes: null, servings: null, source: '',
    intro: '', ingredients: [], steps: [], notes: '', nutrition: '', extra: '', attachments: [],
  };
  const intro: string[] = [];
  const extra: string[] = [];
  let section: string | null = null;
  let sectionLines: string[] = [];

  const flush = () => {
    if (section === null) return;
    const text = sectionLines.join('\n').trim();
    const key = section.toLowerCase();
    if (key === 'ingredients') doc.ingredients = sectionLines.map(bullet).filter(Boolean);
    else if (STEP_HEADINGS.has(key)) doc.steps = sectionLines.map(bullet).filter(Boolean);
    else if (key === 'notes') doc.notes = text;
    else if (key === 'nutrition') doc.nutrition = text;
    else extra.push(`## ${section}\n${text}`);
  };

  for (const line of body.split('\n')) {
    if (ATTACHMENT_LINE.test(line.trim())) {
      doc.attachments.push(line.trim());
      continue;
    }
    const heading = /^##\s+(.+?)\s*$/.exec(line);
    if (heading) {
      flush();
      section = heading[1];
      sectionLines = [];
      continue;
    }
    if (section !== null) {
      sectionLines.push(line);
      continue;
    }
    const tags = /^Tags:\s*(.*)$/i.exec(line);
    const times = /^Times:\s*(.*)$/i.exec(line);
    const source = /^Source:\s*(.*)$/i.exec(line);
    if (tags) doc.tags = (tags[1].match(/#[A-Za-z0-9][A-Za-z0-9-]*/g) ?? []).map((t) => t.slice(1).toLowerCase());
    else if (times) {
      doc.prepMinutes = minutes(times[1], 'prep');
      doc.cookMinutes = minutes(times[1], 'cook');
      doc.totalMinutes = minutes(times[1], 'total');
      const serves = /(?:servings?|serves)\D{0,3}(\d{1,3})/i.exec(times[1]);
      doc.servings = serves ? Number(serves[1]) : null;
    } else if (source) doc.source = source[1].trim();
    else if (!/^(Added by|Image):/i.test(line)) intro.push(line);
  }
  flush();
  doc.intro = intro.join('\n').trim();
  doc.extra = extra.join('\n\n');
  if (doc.totalMinutes === null && (doc.prepMinutes || doc.cookMinutes)) {
    doc.totalMinutes = (doc.prepMinutes ?? 0) + (doc.cookMinutes ?? 0);
  }
  return doc;
}

export function buildRecipeDoc(doc: RecipeDoc): string {
  const lines: string[] = [];
  if (doc.tags.length) lines.push(`Tags: ${doc.tags.map((t) => `#${t}`).join(' ')}`);
  const times = [
    doc.prepMinutes ? `prep ${doc.prepMinutes} min` : '',
    doc.cookMinutes ? `cook ${doc.cookMinutes} min` : '',
    doc.totalMinutes && doc.totalMinutes !== (doc.prepMinutes ?? 0) + (doc.cookMinutes ?? 0)
      ? `total ${doc.totalMinutes} min`
      : '',
    doc.servings ? `servings ${doc.servings}` : '',
  ].filter(Boolean);
  if (times.length) lines.push(`Times: ${times.join(' · ')}`);
  if (doc.source) lines.push(`Source: ${doc.source}`);
  if (doc.intro) lines.push('', doc.intro);
  lines.push('', '## Ingredients', ...doc.ingredients.filter((i) => i.trim()).map((i) => `- [ ] ${i.trim()}`));
  lines.push('', '## Steps', ...doc.steps.filter((s) => s.trim()).map((s, i) => `${i + 1}. ${s.trim()}`));
  if (doc.notes.trim()) lines.push('', '## Notes', doc.notes.trim());
  if (doc.extra.trim()) lines.push('', doc.extra.trim());
  if (doc.nutrition.trim()) lines.push('', '## Nutrition', doc.nutrition.trim());
  if (doc.attachments.length) lines.push('', ...doc.attachments);
  return lines.join('\n').replace(/^\n+/, '');
}

/** Meals and proteins as the shelf names them (the server's facets). */
export const MEAL_LABELS: Record<string, string> = {
  breakfast: 'Breakfast', main: 'Main dish', soup: 'Soup & chili', salad: 'Salad', side: 'Side',
  bread: 'Bread', dessert: 'Dessert', snack: 'Snack', drink: 'Drink',
};
export const PROTEIN_LABELS: Record<string, string> = {
  chicken: 'Chicken', beef: 'Beef', pork: 'Pork', turkey: 'Turkey', seafood: 'Seafood', lamb: 'Lamb',
  vegetarian: 'Meatless',
};
export const MEAL_EMOJI: Record<string, string> = {
  breakfast: '🥞', main: '🍽️', soup: '🍲', salad: '🥗', side: '🥔', bread: '🍞', dessert: '🍪', snack: '🥨', drink: '🥤',
};

/** "25 min", "1 h 10", "4 h": a recipe's time the way a cook says it. */
export function formatCookTime(minutes: number | null | undefined): string {
  if (!minutes || minutes <= 0) return '';
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest}` : `${hours} h`;
}

/** A recipe name to compare by: "Beef Stew" and an older "beef-stew" are one recipe. */
export function recipeTitleKey(title: string): string {
  return title.replace(/[-_]+/g, ' ').toLowerCase().split(/\s+/).filter(Boolean).join(' ');
}
