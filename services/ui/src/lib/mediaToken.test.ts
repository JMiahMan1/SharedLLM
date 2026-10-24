import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  REFRESH_LEAD_SECONDS,
  currentMediaToken,
  ensureMediaToken,
  refreshMediaToken,
  resetMediaTokenForTests,
} from './mediaToken';
import { proxiedImage } from './mediaImage';

function tokenResponse(token: string, expiresAt: number, user = 'alice') {
  return new Response(JSON.stringify({ token, expires_at: expiresAt, user }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

const nowSeconds = () => Math.floor(Date.now() / 1000);

describe('media token', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    resetMediaTokenForTests();
    localStorage.setItem('jarvis_api_key', 'key-1');
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    resetMediaTokenForTests();
    localStorage.removeItem('jarvis_api_key');
  });

  it('mints a token with the API key in the header, never in the URL', async () => {
    fetchMock.mockResolvedValue(tokenResponse('t1', nowSeconds() + 3600));

    const token = await refreshMediaToken();

    expect(token).toMatchObject({ token: 't1', user: 'alice' });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/media\/token$/);
    expect(String(url)).not.toContain('key-1');
    expect(init.headers.Authorization).toBe('Bearer key-1');
  });

  it('shares one request between concurrent callers', async () => {
    fetchMock.mockResolvedValue(tokenResponse('t1', nowSeconds() + 3600));

    await Promise.all([ensureMediaToken(), ensureMediaToken(), refreshMediaToken()]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('does not ask without a signed-in API key', async () => {
    localStorage.removeItem('jarvis_api_key');

    expect(await ensureMediaToken()).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('drops the token when the API key changes', async () => {
    fetchMock.mockResolvedValue(tokenResponse('t1', nowSeconds() + 3600));
    await refreshMediaToken();

    localStorage.setItem('jarvis_api_key', 'key-2');

    expect(currentMediaToken()).toBeNull();
  });

  it('refreshes five minutes before it expires', async () => {
    vi.useFakeTimers();
    fetchMock
      .mockResolvedValueOnce(tokenResponse('t1', nowSeconds() + 3600))
      .mockResolvedValueOnce(tokenResponse('t2', nowSeconds() + 7200));
    await refreshMediaToken();

    await vi.advanceTimersByTimeAsync((3600 - REFRESH_LEAD_SECONDS - 1) * 1000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2000);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(currentMediaToken()?.token).toBe('t2');
  });

  it('holds nothing when the gateway refuses', async () => {
    fetchMock.mockResolvedValue(new Response('no', { status: 401 }));

    expect(await refreshMediaToken()).toBeNull();
    expect(currentMediaToken()).toBeNull();
  });

  it('signs cover URLs with user and mt, never the API key', async () => {
    fetchMock.mockResolvedValue(tokenResponse('t1', nowSeconds() + 3600));
    expect(proxiedImage('/imageproxy/cover.jpg')).toBeNull();
    await ensureMediaToken();

    const url = proxiedImage('/imageproxy/cover.jpg') ?? '';

    expect(url).toContain(`/api/media/imageproxy?path=${encodeURIComponent('/imageproxy/cover.jpg')}`);
    expect(url).toContain('&user=alice&mt=t1');
    expect(url).not.toContain('token=');
    expect(url).not.toContain('key-1');
  });
});
