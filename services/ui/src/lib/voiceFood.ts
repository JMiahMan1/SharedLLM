/**
 * Voice food logging: a spoken sentence ("two eggs and a slice of toast for
 * breakfast") split into the foods to look up.
 *
 * Only splitting, never nutrition: each item is then searched in the food
 * database and shown as a draft the person checks before anything is saved.
 * A spoken count ("two eggs") becomes the servings, never a gram weight.
 */
import type { Meal } from './healthHub';

export interface SpokenFood {
  /** What to search for, e.g. "eggs". */
  query: string;
  /** A spoken count ("two", "3", "half a"), else 1. */
  servings: number;
  /** The words as heard, for the draft row. */
  heard: string;
}

const NUMBER_WORDS: Record<string, number> = {
  a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  half: 0.5, couple: 2,
};
const MEALS: Meal[] = ['breakfast', 'lunch', 'dinner', 'snack'];
const FILLER = /^(i\s+(had|ate|have|just\s+had)|for\s+\w+\s+i\s+had|log|add|some|with)\s+/;

/** The meal named in the sentence ("…for lunch"), if any. */
export function spokenMeal(text: string): Meal | null {
  const lower = text.toLowerCase();
  return MEALS.find((meal) => new RegExp(`\\b${meal}\\b`).test(lower)) ?? null;
}

/** The foods in a spoken sentence, in order. */
export function splitSpokenFoods(text: string): SpokenFood[] {
  const cleaned = text
    .toLowerCase()
    .replace(/\b(for|at|with)\s+(breakfast|lunch|dinner|a snack|snack)\b/g, ' ')
    .replace(/[.!?]/g, ' ');
  return cleaned
    .split(/,|\band\b|\bplus\b|\bthen\b/)
    .map((part) => part.trim().replace(/\s+/g, ' '))
    .map((part) => part.replace(FILLER, '').trim())
    .filter((part) => part.length > 1)
    .map((part) => {
      const words = part.split(' ');
      let servings = 1;
      const first = words[0];
      if (/^\d+(\.\d+)?$/.test(first)) {
        servings = Number(first);
        words.shift();
      } else if (first in NUMBER_WORDS) {
        servings = NUMBER_WORDS[first];
        words.shift();
        if (words[0] === 'a' || words[0] === 'of') words.shift();
      }
      // "slice of toast", "bowl of oats", "cup of milk": search the food.
      const ofIndex = words.indexOf('of');
      const query = (ofIndex > 0 && ofIndex < words.length - 1 ? words.slice(ofIndex + 1) : words).join(' ').trim();
      return { query, servings, heard: part };
    })
    .filter((item) => item.query.length > 1);
}
