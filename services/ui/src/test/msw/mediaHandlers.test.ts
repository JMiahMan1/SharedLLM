import { describe, it, expect } from 'vitest';

describe('media msw handlers', () => {
  it('serves the Listen Now payload from fixtures', async () => {
    const res = await fetch('/api/media/home');
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.recent[0].name).toBe('Midnight Drive');
    expect(data.errors).toEqual({ ma: null, abs: null });
  });

  it('serves Audiobookshelf books in progress from fixtures', async () => {
    const res = await fetch('/api/media/audiobookshelf/last-played');
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.books.map((b: { id: string }) => b.id)).toContain('book-1');
  });

  it('serves media status from fixtures', async () => {
    const res = await fetch('/execute/media/status', { method: 'POST' });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.status).toBe('SUCCESS');
    expect(data.detail.all_players.length).toBeGreaterThan(0);
  });
});
