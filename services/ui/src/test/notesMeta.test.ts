import { describe, it, expect, beforeEach } from 'vitest';
import {
  EMPTY_NOTES_INDEX,
  archivedPathFor,
  clearLegacyMeta,
  forgetIndexEntry,
  forgetNoteMeta,
  getNoteMeta,
  indexEntry,
  isArchivedPath,
  isTrashedPath,
  mergeLegacyMeta,
  notePreview,
  parseChecklist,
  parseNotesIndex,
  readNoteMetaMap,
  setIndexEntry,
  setNoteMeta,
  sortNotes,
  sortNotesByIndex,
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

  it('sorts from a metadata map read once', () => {
    const notes = [
      { title: 'B', path: 'Notes/b.md' },
      { title: 'A', path: 'Notes/a.md' },
    ];
    setNoteMeta('Notes/a.md', { pinned: true, color: 'sky' });
    const meta = readNoteMetaMap();
    expect(meta['Notes/a.md']).toEqual({ pinned: true, color: 'sky' });
    expect(sortNotes(notes, meta).map((n) => n.title)).toEqual(['A', 'B']);
  });

  it('parses the server index from an object or JSON text and rejects junk', () => {
    const index = { version: 1, notes: { 'Notes/a.md': { pinned: true } } };
    expect(parseNotesIndex(index)).toEqual(index);
    expect(parseNotesIndex(JSON.stringify(index))).toEqual(index);
    expect(parseNotesIndex('not json')).toBeNull();
    expect(parseNotesIndex({ version: 1, notes: [] })).toBeNull();
  });

  it('keeps entries only while they carry a pin, colour or origin', () => {
    const index = setIndexEntry(EMPTY_NOTES_INDEX, 'Notes/a.md', { pinned: true, color: 'rose' });
    expect(indexEntry(index, 'Notes/a.md')).toMatchObject({ pinned: true, color: 'rose' });

    const cleared = setIndexEntry(index, 'Notes/a.md', { pinned: false, color: 'default' });
    expect(cleared.notes['Notes/a.md']).toBeUndefined();

    const archived = setIndexEntry(cleared, 'Notes/a.md', { previousPath: 'Notes/a.md' });
    expect(indexEntry(archived, 'Notes/a.md')?.previousPath).toBe('Notes/a.md');
    expect(forgetIndexEntry(archived, 'Notes/a.md').notes['Notes/a.md']).toBeUndefined();
  });

  it('recognises archive and trash paths and targets their folders', () => {
    expect(archivedPathFor('Notes/Trip ideas.md')).toBe('Notes/Archive/Trip ideas.md');
    expect(archivedPathFor('Notes/Archive/Trip ideas.md')).toBe('Notes/Archive/Trip ideas.md');
    expect(isArchivedPath('Notes/Archive/Trip ideas.md')).toBe(true);
    expect(isTrashedPath('Notes/Trash/Trip ideas.md')).toBe(true);
    expect(isArchivedPath('Notes/Trip ideas.md')).toBe(false);
  });

  it('migrates the on-device pins into the server index exactly once', () => {
    setNoteMeta('Notes/a.md', { pinned: true, color: 'amber' });
    setNoteMeta('Notes/b.md', { pinned: true });
    const notes = [
      { title: 'A', path: 'Notes/a.md' },
      { title: 'B', path: 'Notes/b.md' },
      { title: 'C', path: 'Notes/c.md' },
    ];
    const merged = mergeLegacyMeta(
      { version: 1, notes: { 'Notes/a.md': { color: 'sky' } } },
      notes,
    );
    // An entry already in the index wins; the other local pin is preserved.
    expect(merged.notes['Notes/a.md']).toEqual({ color: 'sky' });
    expect(merged.notes['Notes/b.md']).toEqual({ pinned: true });
    expect(merged.notes['Notes/c.md']).toBeUndefined();

    clearLegacyMeta();
    expect(localStorage.getItem('jarvis_notes_meta_v1')).toBeNull();
  });

  it('sorts the server index with pinned notes first', () => {
    const notes = [
      { title: 'B', path: 'Notes/b.md' },
      { title: 'A', path: 'Notes/a.md' },
    ];
    const index = setIndexEntry(EMPTY_NOTES_INDEX, 'Notes/b.md', { pinned: true });
    expect(sortNotesByIndex(notes, index).map((n) => n.title)).toEqual(['B', 'A']);
  });
});
