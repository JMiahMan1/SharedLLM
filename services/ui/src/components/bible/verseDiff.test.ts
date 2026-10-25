import { describe, expect, it } from 'vitest';
import { DIFF_THRESHOLD, diffVerses, differingKeys, normaliseWords, wordSimilarity } from './verseDiff';
import type { BibleVerse } from '../../types/api';

const v = (n: number, text: string, chapter = 3): BibleVerse => ({
  version: 'x',
  osis: 'John',
  book_name: 'John',
  chapter,
  verse: n,
  reference: `John ${chapter}:${n}`,
  text,
});

describe('normaliseWords', () => {
  it('drops case, punctuation, accents, apostrophes and extra whitespace', () => {
    expect(normaliseWords('  “Lord,” said Peter —   it’s GOOD; naïve!  ')).toEqual([
      'lord', 'said', 'peter', 'its', 'good', 'naive',
    ]);
    expect(normaliseWords(' ,.; ')).toEqual([]);
  });
});

describe('wordSimilarity', () => {
  it('is 1 for wording that differs only in case and punctuation', () => {
    expect(wordSimilarity('In the beginning, God created.', 'in the beginning God created')).toBe(1);
  });

  it('keeps small wording changes above the threshold', () => {
    const genesis = wordSimilarity(
      'In the beginning God created the heaven and the earth.',
      'In the beginning God created the heavens and the earth.',
    );
    expect(genesis).toBeCloseTo(0.9, 5);
    const john = wordSimilarity(
      'For God so loved the world, that he gave his only begotten Son, that whosoever believeth in him should not perish, but have everlasting life.',
      'For God so loved the world, that he gave his one and only Son, that whoever believes in him should not perish, but have eternal life.',
    );
    expect(john).toBeGreaterThan(DIFF_THRESHOLD);
    expect(john).toBeLessThan(0.85);
  });

  it('counts repeated words only as often as both sides have them', () => {
    expect(wordSimilarity('the the the', 'the')).toBeCloseTo(0.5, 5);
  });

  it('is 0 when one side has no words', () => {
    expect(wordSimilarity('', 'word')).toBe(0);
    expect(wordSimilarity('', '')).toBe(1);
  });
});

describe('diffVerses', () => {
  it('marks a materially different verse and leaves near-identical ones alone', () => {
    const diff = diffVerses(
      [v(1, 'And it came to pass in those days.'), v(2, 'Jesus wept.')],
      [v(1, 'Some time later a decree went out.'), v(2, 'Jesus wept!')],
    );
    expect(diff.get('3:1')?.status).toBe('differs');
    expect(diff.get('3:2')).toEqual({ status: 'same', similarity: 1 });
    expect(differingKeys(diff)).toEqual(new Set(['3:1']));
  });

  it('never calls a missing verse a difference', () => {
    const diff = diffVerses(
      [v(20, 'Because of your unbelief.', 17), v(21, 'Howbeit this kind goeth not out but by prayer and fasting.', 17)],
      [v(20, 'Because you have so little faith.', 17), v(22, 'When they came together in Galilee.', 17), v(23, '   ', 17)],
    );
    expect(diff.get('17:21')).toEqual({ status: 'only-primary', similarity: null });
    expect(diff.get('17:22')).toEqual({ status: 'only-compare', similarity: null });
    // A verse with no words is missing, not empty-versus-text.
    expect(diff.has('17:23')).toBe(false);
    expect(differingKeys(diff).has('17:21')).toBe(false);
    expect(differingKeys(diff).has('17:22')).toBe(false);
  });

  it('keys verses by chapter so a passage across chapters does not collide', () => {
    const diff = diffVerses([v(1, 'Same words here', 3), v(1, 'Other words', 4)], [v(1, 'Same words here', 3)]);
    expect(diff.get('3:1')?.status).toBe('same');
    expect(diff.get('4:1')?.status).toBe('only-primary');
  });

  it('honours a custom threshold', () => {
    const primary = [v(1, 'a b c d')];
    const compare = [v(1, 'a b x y')];
    expect(diffVerses(primary, compare, 0.4).get('3:1')?.status).toBe('same');
    expect(diffVerses(primary, compare, 0.6).get('3:1')?.status).toBe('differs');
  });
});
