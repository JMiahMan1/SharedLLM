import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  EMPTY_NOTES_INDEX,
  TRASH_PREFIX,
  archivedPathFor,
  clearLegacyMeta,
  extractTags,
  forgetIndexEntry,
  forgetNoteMeta,
  getNoteMeta,
  hasTag,
  indexEntry,
  isArchivedPath,
  isTrashedPath,
  matchesNoteQuery,
  mergeLegacyMeta,
  noteKind,
  notePreview,
  parseChecklist,
  parseNoteQuery,
  parseNotesIndex,
  readNoteMetaMap,
  readNotesSnapshot,
  setIndexEntry,
  setNoteMeta,
  sortNotes,
  sortNotesByIndex,
  topTags,
  writeNotesSnapshot,
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
    expect(archivedPathFor('Notes/Work/Plan.md')).toBe('Notes/Archive/Work/Plan.md');
    expect(archivedPathFor('Notes/Archive/Trip ideas.md')).toBe('Notes/Archive/Trip ideas.md');
    expect(archivedPathFor('Notes/Trip ideas.md', TRASH_PREFIX)).toBe('Notes/Trash/Trip ideas.md');
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

  it('reads inline #labels, lowercased and deduped', () => {
    expect(extractTags('# Trip\n\nVisit #Sedona, #sedona and (C#) code')).toEqual(['sedona']);
    expect(extractTags(undefined)).toEqual([]);
    expect(hasTag('- [ ] milk #groceries', 'GROCERIES')).toBe(true);
  });

  it('tells checklists from prose', () => {
    expect(noteKind('- [ ] milk')).toBe('list');
    expect(noteKind('just words')).toBe('note');
    expect(noteKind(undefined)).toBe('note');
  });

  it('parses Keep-style search operators', () => {
    expect(parseNoteQuery('tag:Travel label:food colour:rose type:list beach')).toEqual({
      text: 'beach',
      tags: ['travel', 'food'],
      colors: ['rose'],
      kinds: ['list'],
    });
  });

  it('matches notes on labels, colour, kind and text', () => {
    const note = { title: 'Packing' };
    const content = '# Packing\nCategory: Notes\n\n- [ ] socks #travel';
    expect(matchesNoteQuery(note, content, parseNoteQuery('tag:travel'), 'sky')).toBe(true);
    expect(matchesNoteQuery(note, content, parseNoteQuery('type:list'), 'sky')).toBe(true);
    expect(matchesNoteQuery(note, content, parseNoteQuery('type:note'), 'sky')).toBe(false);
    expect(matchesNoteQuery(note, content, parseNoteQuery('colour:sky'), 'sky')).toBe(true);
    expect(matchesNoteQuery(note, content, parseNoteQuery('colour:rose'), 'sky')).toBe(false);
    expect(matchesNoteQuery(note, content, parseNoteQuery('socks'), 'sky')).toBe(true);
    expect(matchesNoteQuery(note, content, parseNoteQuery('umbrella'), 'sky')).toBe(false);
  });

  it('counts labels per shelf, most used first', () => {
    const notes = [{ path: 'a' }, { path: 'b' }];
    const contents = { a: '#x #y', b: '#x' };
    expect(topTags(notes, contents)).toEqual([
      { tag: 'x', count: 2 },
      { tag: 'y', count: 1 },
    ]);
  });

  it('keeps a visit in a snapshot for the next visit to paint', () => {
    const index = setIndexEntry(EMPTY_NOTES_INDEX, 'Notes/A.md', { pinned: true });
    writeNotesSnapshot('me', [{ title: 'A', path: 'Notes/A.md', content: '# A\nCategory: Notes\n\nbody' }], index);

    const snapshot = readNotesSnapshot('me');
    expect(snapshot).not.toBeNull();
    expect(snapshot?.notes).toHaveLength(1);
    expect(indexEntry(snapshot?.index, 'Notes/A.md').pinned).toBe(true);
    expect(typeof snapshot?.savedAt).toBe('number');
    // The admin "send as" switch keeps its own snapshot.
    expect(readNotesSnapshot('admin')).toBeNull();
  });

  it('ignores a corrupt snapshot', () => {
    localStorage.setItem('jarvis_notes_snapshot_v1:me', '{oops');
    expect(readNotesSnapshot('me')).toBeNull();
    localStorage.setItem(
      'jarvis_notes_snapshot_v1:me',
      JSON.stringify({ savedAt: 1, notes: [], index: { version: 1, notes: [] } }),
    );
    expect(readNotesSnapshot('me')).toBeNull();
  });

  it('drops the note bodies when storage is full', () => {
    const setItem = vi.spyOn(localStorage, 'setItem');
    setItem.mockImplementationOnce(() => {
      throw new Error('quota');
    });
    writeNotesSnapshot('me', [{ title: 'A', path: 'Notes/A.md', content: 'big' }], EMPTY_NOTES_INDEX);
    setItem.mockRestore();

    const snapshot = readNotesSnapshot('me');
    expect(snapshot?.notes).toEqual([{ title: 'A', path: 'Notes/A.md' }]);
  });
});
