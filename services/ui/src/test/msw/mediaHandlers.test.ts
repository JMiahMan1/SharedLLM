import { describe, it, expect } from 'vitest';

describe('media msw handlers', () => {
  it('serves music-assistant recent from fixtures', async () => {
    const res = await fetch('/api/media/music-assistant/recent');
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.status).toBe('ok');
    expect(data.recent.length).toBeGreaterThan(0);
    expect(data.recent[0].name).toBe('Midnight Drive');
  });

  it('serves audiobookshelf libraries from fixtures', async () => {
    const res = await fetch('/api/media/audiobookshelf/libraries');
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.libraries.map((l: { id: string }) => l.id)).toContain('lib-books');
  });

  it('serves media status from fixtures', async () => {
    const res = await fetch('/execute/media/status', { method: 'POST' });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.status).toBe('SUCCESS');
    expect(data.detail.all_players.length).toBeGreaterThan(0);
  });
});
