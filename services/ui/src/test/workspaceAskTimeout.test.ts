/**
 * The workspace composer's turn is billed to the model, so it outlives axios's
 * 15s default by minutes. Without an override every question was aborted
 * client-side before the server answered, which is what made the composer look
 * broken while the gateway log showed a 200 for the same request seconds later.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { api, apiClient } from '../services/api';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('workspace composer timing', () => {
  it('waits for the model instead of the axios default', async () => {
    const post = vi
      .spyOn(apiClient, 'post')
      .mockResolvedValue({ data: { status: 'SUCCESS', answer: 'A' } });

    await api.askWorkspace('ws-1', 'what did he say?', 'librarian');

    expect(post).toHaveBeenCalledTimes(1);
    const [url, body, config] = post.mock.calls[0];
    expect(url).toBe('/api/workspaces/ws-1/ask');
    expect(body).toEqual({ query: 'what did he say?', mode: 'librarian' });
    expect(config?.timeout).toBe(640_000);
    expect(config?.timeout).toBeGreaterThan(60_000);
  });

  it('gives a non-streaming assistant turn the same room', async () => {
    const post = vi
      .spyOn(apiClient, 'post')
      .mockResolvedValue({ data: { status: 'SUCCESS', response: 'A' } });

    await api.chat('turn the porch light on');

    const [, , config] = post.mock.calls[0];
    expect(config?.timeout).toBe(640_000);
  });
});
