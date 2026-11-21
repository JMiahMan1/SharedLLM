import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { apiClient, resetSignOutLatchForTests } from '../services/api';

/**
 * Regression cover for the login loop: a pre-auth 401 used to clear the
 * session and redirect to /login, which made the app bounce on every start.
 */
describe('auth redirect handling', () => {
  beforeEach(() => {
    resetSignOutLatchForTests();
    localStorage.setItem('jarvis_api_key', 'stored-token');
    localStorage.setItem('jarvis_user', 'alex');
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('keeps the session when an optional request 401s', async () => {
    server.use(
      http.get('/api/users/me/theme', () => new HttpResponse(null, { status: 401 }))
    );

    await expect(apiClient.get('/api/users/me/theme', { skipAuthRedirect: true })).rejects.toBeTruthy();

    expect(localStorage.getItem('jarvis_api_key')).toBe('stored-token');
    expect(localStorage.getItem('jarvis_user')).toBe('alex');
  });

  it('still clears the session when the server refuses the key', async () => {
    server.use(http.get('/api/users/me', () => HttpResponse.json({ detail: 'Invalid API Key', code: 'api_key_invalid' }, { status: 401 })));

    await expect(apiClient.get('/api/users/me')).rejects.toBeTruthy();

    expect(localStorage.getItem('jarvis_api_key')).toBeNull();
    expect(localStorage.getItem('jarvis_user')).toBeNull();
  });

  it('keeps the session when a 401 is not a refusal of the key', async () => {
    server.use(http.get('/api/users/me', () => new HttpResponse(null, { status: 401 })));

    await expect(apiClient.get('/api/users/me')).rejects.toBeTruthy();

    expect(localStorage.getItem('jarvis_api_key')).not.toBeNull();
  });
});
