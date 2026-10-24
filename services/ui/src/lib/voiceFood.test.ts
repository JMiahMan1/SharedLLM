import { describe, expect, it } from 'vitest';
import { splitSpokenFoods, spokenMeal } from './voiceFood';

describe('splitSpokenFoods', () => {
  it('splits a sentence into foods with spoken counts', () => {
    expect(splitSpokenFoods('I had two eggs and a slice of toast, plus 3 strawberries for breakfast')).toEqual([
      { query: 'eggs', servings: 2, heard: 'two eggs' },
      { query: 'toast', servings: 1, heard: 'a slice of toast' },
      { query: 'strawberries', servings: 3, heard: '3 strawberries' },
    ]);
  });

  it('keeps half portions and drops empty pieces', () => {
    expect(splitSpokenFoods('half a banana and, and')).toEqual([{ query: 'banana', servings: 0.5, heard: 'half a banana' }]);
    expect(splitSpokenFoods('')).toEqual([]);
  });

  it('finds the meal named in the sentence', () => {
    expect(spokenMeal('oatmeal for breakfast')).toBe('breakfast');
    expect(spokenMeal('an apple')).toBeNull();
  });
});
