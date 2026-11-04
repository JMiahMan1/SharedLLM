import { describe, it, expect, vi, beforeEach } from 'vitest';
import { saveNote } from '../lib/noteSave';
import { api } from '../services/api';

vi.mock('../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, api: { ...(actual.api as Record<string, unknown>), writeNote: vi.fn(), deleteNote: vi.fn() } };
});

beforeEach(() => vi.clearAllMocks());

describe('saveNote', () => {
  it('gives a new note a free name instead of replacing one already there', async () => {
    vi.mocked(api.writeNote)
      .mockResolvedValueOnce({ status: 'FAILURE', message: 'taken', detail: { conflict: true, exists: true } } as never)
      .mockResolvedValueOnce({ status: 'SUCCESS', message: 'ok', detail: { path: 'Notes/Groceries 2.md', etag: '"e"' } } as never);
    const saved = await saveNote({ title: 'Groceries', content: 'milk', category: 'Notes' });
    expect(saved.title).toBe('Groceries 2');
    expect(saved.conflict).toBe(false);
    expect(vi.mocked(api.writeNote).mock.calls.map(([p]) => [p.title, p.if_none_match])).toEqual([
      ['Groceries', true],
      ['Groceries 2', true],
    ]);
  });

  it('saves a note from Nextcloud Notes back without a header, in place', async () => {
    vi.mocked(api.writeNote).mockResolvedValueOnce({ status: 'SUCCESS', message: 'ok', detail: { path: 'Notes/Trip.md' } } as never);
    await saveNote({ title: 'Trip', content: 'a\n  b', category: 'Notes', previous: { title: 'Trip', category: 'Notes', path: 'Notes/Trip.md' }, plain: true, ifMatch: '"v1"' });
    expect(vi.mocked(api.writeNote).mock.calls[0][0]).toMatchObject({ path: 'Notes/Trip.md', plain: true, if_match: '"v1"' });
    expect(vi.mocked(api.writeNote).mock.calls[0][0]).not.toHaveProperty('if_none_match');
  });
});
