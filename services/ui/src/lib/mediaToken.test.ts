import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  REFRESH_LEAD_SECONDS,
  currentMediaToken,
  ensureMediaToken,
  refreshMediaToken,
  resetMediaTokenForTests,
} from './mediaToken';
import { proxiedImage } from './mediaImage';
import { apiClient } from '../services/api';

vi.mock('../services/api', () => ({ apiClient: { post: vi.fn() } }));

function tokenResponse(token: string, expiresAt: number, user = 'alice') {
  return { data: { token, expires_at: expiresAt, user } };
}

const nowSeconds = () => Math.floor(Date.now() / 1000);

describe('media token', () => {
  let fetchMock: ReturnType<typeof vi.mocked<typeof apiClient.post>>;

  beforeEach(() => {
    resetMediaTokenForTests();
    localStorage.setItem('jarvis_api_key', 'key-1');
    fetchMock = vi.mocked(apiClient.post);
    fetchMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    resetMediaTokenForTests();
    localStorage.removeItem('jarvis_api_key');
  });

  it('mints a token through the shared API client (bearer header), never with the key in the URL', async () => {
    fetchMock.mockResolvedValue(tokenResponse('t1', nowSeconds() + 3600));

    const token = await refreshMediaToken();

    expect(token).toMatchObject({ token: 't1', user: 'alice' });
    expect(fetchMock).toHaveBeenCalledWith('/api/media/token');
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
    fetchMock.mockRejectedValue(new Error('Request failed with status code 401'));

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
