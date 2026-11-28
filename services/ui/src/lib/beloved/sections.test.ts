import { describe, expect, it } from 'vitest';
import { BELOVED_TABS, belovedTab } from './sections';

/**
 * A notice names a section of Beloved ("a love note", "a date idea"). The server
 * sends the section it means; this turns it into the tab the page opens, and it
 * has to be forgiving about the words a link, a phrasebook or a future emitter
 * might use, while never inventing a section out of nonsense.
 */
describe('the section a Beloved notice asks for', () => {
  it('reads the sections the server sends', () => {
    expect(belovedTab('home')).toBe('home');
    expect(belovedTab('days')).toBe('days');
    expect(belovedTab('today')).toBe('today');
    expect(belovedTab('notes')).toBe('notes');
    expect(belovedTab('dates')).toBe('dates');
    expect(belovedTab('justus')).toBe('justus');
  });

  it('keeps the plain page meaning the home screen', () => {
    // `beloved=1` is what every notice sent before sections existed.
    expect(belovedTab('1')).toBe('home');
  });

  it('accepts the singular and the synonym', () => {
    expect(belovedTab('note')).toBe('notes');
    expect(belovedTab('prayer')).toBe('notes');
    expect(belovedTab('idea')).toBe('dates');
    expect(belovedTab('plan')).toBe('justus');
    expect(belovedTab('us')).toBe('home');
    expect(belovedTab('just-us')).toBe('justus');
  });

  it('reads a link written by hand', () => {
    expect(belovedTab(' Notes ')).toBe('notes');
    expect(belovedTab('DATES')).toBe('dates');
  });

  it('asks for nothing when there is nothing to ask for', () => {
    expect(belovedTab(null)).toBeNull();
    expect(belovedTab(undefined)).toBeNull();
    expect(belovedTab('')).toBeNull();
    expect(belovedTab('recipes')).toBeNull();
  });

  it('names every tab the bar shows', () => {
    expect(BELOVED_TABS.map((t) => t.id)).toEqual(['home', 'days', 'today', 'notes', 'dates', 'justus']);
    expect(BELOVED_TABS.every((t) => t.label.length > 0 && t.label.length < 14)).toBe(true);
  });
});
