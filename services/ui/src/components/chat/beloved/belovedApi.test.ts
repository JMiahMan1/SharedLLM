import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AxiosResponse } from 'axios';
import { api, apiClient } from '../../../services/api';

vi.mock('axios', async () => {
  const actual = await vi.importActual('axios');
  return {
    ...actual,
    default: {
      create: vi.fn(() => ({
        interceptors: {
          request: { use: vi.fn(), eject: vi.fn() },
          response: { use: vi.fn(), eject: vi.fn() },
        },
        get: vi.fn(),
        post: vi.fn(),
        put: vi.fn(),
        patch: vi.fn(),
        delete: vi.fn(),
      })),
    },
  };
});

const reply = (data: unknown) => ({ data } as AxiosResponse);

describe('Beloved metadata API calls', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('posts only a kind to /seen and fetches activity metadata', async () => {
    vi.mocked(apiClient.post).mockResolvedValue(reply({ shared: true, notified: false }));
    vi.mocked(apiClient.get).mockResolvedValue(reply({ me: 'partnerA', activity: [{ who: 'partnerB', what: 'note', at: 10 }] }));

    await expect(api.belovedSeen('room one', 'page')).resolves.toEqual({ shared: true, notified: false });
    expect(apiClient.post).toHaveBeenCalledWith('/api/private-chat/chats/room%20one/beloved/seen', { what: 'page' });
    for (const what of ['gallery', 'note', 'prayer', 'idea'] as const) {
      await api.belovedSeen('room one', what);
      const body = vi.mocked(apiClient.post).mock.calls.at(-1)?.[1];
      expect(body).toEqual({ what });
      expect(JSON.stringify(body)).not.toContain('sealed');
    }

    const activity = await api.belovedActivity('room one');
    expect(apiClient.get).toHaveBeenCalledWith('/api/private-chat/chats/room%20one/beloved/activity');
    expect(activity.activity[0]).toEqual({ who: 'partnerB', what: 'note', at: 10 });
  });

  it('reads and writes preferences without any sealed content', async () => {
    const prefs = { share_activity: false, encouragements: true, encourage_hour: 8 };
    vi.mocked(apiClient.get).mockResolvedValue(reply(prefs));
    vi.mocked(apiClient.put).mockResolvedValue(reply(prefs));

    await expect(api.getBelovedPrefs('tok')).resolves.toEqual(prefs);
    expect(apiClient.get).toHaveBeenCalledWith('/api/private-chat/chats/tok/beloved/prefs');
    await expect(api.setBelovedPrefs('tok', { encourage_hour: 8 })).resolves.toEqual(prefs);
    expect(apiClient.put).toHaveBeenCalledWith('/api/private-chat/chats/tok/beloved/prefs', { encourage_hour: 8 });
    expect(JSON.stringify(vi.mocked(apiClient.put).mock.calls[0][1])).not.toContain('sealed');
  });

  it('sends notify on record updates and defaults it to false', async () => {
    vi.mocked(apiClient.put).mockResolvedValue(reply({ id: 'abc', notified: true }));

    const updated = await api.updateBelovedRecord('tok', 'note', 'abc', 'sealed-text', true);
    expect(apiClient.put).toHaveBeenCalledWith('/api/private-chat/chats/tok/beloved/note/abc', { sealed: 'sealed-text', notify: true });
    expect(updated.notified).toBe(true);

    await api.updateBelovedRecord('tok', 'note', 'abc', 'sealed-text');
    expect(apiClient.put).toHaveBeenLastCalledWith('/api/private-chat/chats/tok/beloved/note/abc', { sealed: 'sealed-text', notify: false });

    await api.replaceBelovedRecord('tok', 'note', 'abc', 'sealed-text');
    expect(apiClient.put).toHaveBeenLastCalledWith('/api/private-chat/chats/tok/beloved/note/abc', { sealed: 'sealed-text', notify: false });
  });
});
