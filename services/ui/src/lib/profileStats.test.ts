import { describe, expect, it } from 'vitest';
import {
  bibleQuizLink, bibleReaderLink, firstName, gameLink, minutesText, readQuizChallenge, recipeLink, linkForUse,
} from './profileStats';

describe('profile stat links', () => {
  it('opens the reader where the person left off', () => {
    expect(bibleReaderLink('John 3')).toBe('/bible?tab=read&ref=John%203');
    expect(bibleReaderLink(null)).toBe('/bible?tab=read');
  });

  it("turns someone else's quiz score into a challenge, and your own into the quiz", () => {
    expect(bibleQuizLink({ correct: 8, total: 10 }, 'Robin')).toBe('/bible?tab=play&beat=Robin&score=8&of=10');
    expect(bibleQuizLink({ correct: 8, total: 10 }, null)).toBe('/bible?tab=play');
    expect(bibleQuizLink(null, 'Robin')).toBe('/bible?tab=play');
  });

  it('reads a challenge back, and refuses one that does not add up', () => {
    expect(readQuizChallenge(new URLSearchParams('beat=Robin&score=8&of=10'))).toEqual({ name: 'Robin', score: 8, of: 10 });
    expect(readQuizChallenge(new URLSearchParams('beat=Robin&score=11&of=10'))).toBeNull();
    expect(readQuizChallenge(new URLSearchParams('beat=&score=1&of=10'))).toBeNull();
    expect(readQuizChallenge(new URLSearchParams('beat=Robin&score=x&of=10'))).toBeNull();
    expect(readQuizChallenge(new URLSearchParams(''))).toBeNull();
  });

  it('opens a recipe by its path and a game on its shelf', () => {
    expect(recipeLink('cookbook/dinner/chili.md')).toBe('/recipes?recipe=cookbook%2Fdinner%2Fchili.md');
    expect(gameLink({ slug: 'family-checkers', collection: 'family' })).toBe('/family?tab=games&play=family-checkers');
    expect(gameLink({ slug: 'space-invaders', collection: 'arcade' })).toBe('/family?tab=games');
  });

  it('sends a "used most" label to its page, and nowhere for one it does not know', () => {
    expect(linkForUse('Music, video and podcasts')).toBe('/media');
    expect(linkForUse('Map')).toBe('/wander');
    expect(linkForUse('Something New')).toBeNull();
  });

  it('formats small things plainly', () => {
    expect(minutesText(430)).toBe('7 h 10 min');
    expect(minutesText(null)).toBe('-');
    expect(firstName('Robin Lee')).toBe('Robin');
  });
});
