import { describe, expect, it } from 'vitest';
import { bannerText, getLocalAiStatus, parseLocalAiStatus } from './localAiStatus';
import { http, HttpResponse } from 'msw';
import { server } from '../test/setup';

const OFFLINE = {
  local_ai: { state: 'down', offline: true, since: 1, message: 'offline', still_working: ['home control', 'weather'] },
  backends: { local_ai: { state: 'down' }, arcade: { state: 'down' }, audio: { state: 'up' } },
  any_offline: true,
};

describe('local AI status', () => {
  it('reads the gateway answer', () => {
    expect(parseLocalAiStatus(OFFLINE)).toEqual({
      offline: true, message: 'offline', stillWorking: ['home control', 'weather'], otherOffline: ['games'],
    });
    expect(parseLocalAiStatus({ nope: 1 })).toBeNull();
    expect(parseLocalAiStatus(null)).toBeNull();
  });

  it('says what still works while the thinking brain is offline, in plain words', () => {
    const text = bannerText(parseLocalAiStatus(OFFLINE));
    expect(text).toContain("thinking brain is offline");
    expect(text).toContain('Home control');
    expect(text).toContain('Games is offline too');
    expect(text).not.toMatch(/11434|http|\.local|error/i);
  });

  it('shows nothing when everything is up, and only the other service when just it is down', () => {
    expect(bannerText({ offline: false, message: null, stillWorking: [], otherOffline: [] })).toBeNull();
    expect(bannerText({ offline: false, message: null, stillWorking: [], otherOffline: ['games', 'music making'] }))
      .toBe('Games and music making are offline right now.');
    expect(bannerText(null)).toBeNull();
  });

  it('a status that cannot be read is no banner, never an error', async () => {
    server.use(http.get('/api/status/local-ai', () => new HttpResponse(null, { status: 500 })));
    await expect(getLocalAiStatus()).resolves.toBeNull();
  });
});
