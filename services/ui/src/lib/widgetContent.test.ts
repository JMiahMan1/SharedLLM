import { describe, expect, it } from 'vitest';
import {
  NOTE_MAX_CHARS, PICTURE_DATA_MAX, fitWithin, noteError, pictureSrcError, safeNoteHref, safeNoteImage, safePictureSrc,
} from './widgetContent';

describe('picture sources', () => {
  it('takes https links and small uploaded pictures', () => {
    expect(pictureSrcError('https://photos.example.test/garden.jpg')).toBeNull();
    expect(pictureSrcError('data:image/png;base64,iVBORw0KGgo=')).toBeNull();
    expect(pictureSrcError('')).toBeNull();
  });

  it('refuses scripts, plain http, svg and anything that could break out of the attribute', () => {
    for (const bad of ['javascript:alert(1)', 'http://example.test/a.png', 'data:image/svg+xml;base64,PHN2Zz4=',
      'data:text/html;base64,PGI+', 'https://example.test/a.png" onerror="x', 'https://exa mple.test/a.png', '//example.test/a.png']) {
      expect(pictureSrcError(bad), bad).not.toBeNull();
      expect(safePictureSrc(bad), bad).toBeNull();
    }
    expect(pictureSrcError(`data:image/png;base64,${'A'.repeat(PICTURE_DATA_MAX)}`)).toBe('The uploaded picture is too large');
    expect(safePictureSrc(42)).toBeNull();
  });
});

describe('note links and images', () => {
  it('keeps web, mail, phone and in-app links and drops the rest', () => {
    expect(safeNoteHref('https://example.test')).toBe('https://example.test');
    expect(safeNoteHref('mailto:someone@example.test')).toBe('mailto:someone@example.test');
    expect(safeNoteHref('/rooms')).toBe('/rooms');
    for (const bad of ['javascript:alert(1)', ' JavaScript:x', 'data:text/html,x', 'vbscript:x', '//evil.test']) {
      expect(safeNoteHref(bad), bad).toBeNull();
    }
  });

  it('shows images from https only', () => {
    expect(safeNoteImage('https://example.test/a.png')).toBe('https://example.test/a.png');
    expect(safeNoteImage('http://example.test/a.png')).toBeNull();
    expect(safeNoteImage('data:image/png;base64,AAAA')).toBeNull();
  });

  it('says why a note is too long', () => {
    expect(noteError('Title', 'body')).toBeNull();
    expect(noteError('x'.repeat(81), '')).toMatch(/title/);
    expect(noteError('', 'x'.repeat(NOTE_MAX_CHARS + 1))).toMatch(/note is longer/);
  });
});

describe('fitWithin', () => {
  it('scales the longest side down and never up', () => {
    expect(fitWithin(4000, 3000, 1280)).toEqual({ width: 1280, height: 960 });
    expect(fitWithin(600, 900, 1280)).toEqual({ width: 600, height: 900 });
    expect(fitWithin(0, 10)).toEqual({ width: 0, height: 0 });
  });
});
