import { describe, it, expect, beforeEach } from 'vitest';
import {
  forgetNoteMeta,
  getNoteMeta,
  notePreview,
  parseChecklist,
  setNoteMeta,
  sortNotes,
} from '../lib/notesMeta';

describe('notesMeta', () => {
  beforeEach(() => localStorage.clear());

  it('parses checklist items in both states', () => {
    const items = parseChecklist('- [ ] milk\n- [x] eggs\nplain line');
    expect(items).toEqual([
      { text: 'milk', checked: false },
      { text: 'eggs', checked: true },
    ]);
  });

  it('strips the stored header from previews', () => {
    const preview = notePreview('# My note\nCategory: Notes\n\nBody text here');
    expect(preview).toBe('Body text here');
  });

  it('truncates long previews', () => {
    const preview = notePreview('x'.repeat(500), 10);
    expect(preview).toBe('xxxxxxxxxx…');
  });

  it('persists pin and colour per note', () => {
    setNoteMeta('Notes/a.md', { pinned: true, color: 'rose' });
    expect(getNoteMeta('Notes/a.md')).toMatchObject({ pinned: true, color: 'rose' });
    forgetNoteMeta('Notes/a.md');
    expect(getNoteMeta('Notes/a.md')).toEqual({});
  });

  it('sorts pinned notes first', () => {
    const notes = [
      { title: 'B', path: 'Notes/b.md' },
      { title: 'A', path: 'Notes/a.md' },
    ];
    setNoteMeta('Notes/b.md', { pinned: true });
    expect(sortNotes(notes).map((n) => n.title)).toEqual(['B', 'A']);
  });
});
