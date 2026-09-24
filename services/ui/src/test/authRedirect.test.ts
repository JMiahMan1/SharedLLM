import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { apiClient } from '../services/api';

/**
 * Regression cover for the login loop: a pre-auth 401 used to clear the
 * session and redirect to /login, which made the app bounce on every start.
 */
describe('auth redirect handling', () => {
  beforeEach(() => {
    localStorage.setItem('jarvis_api_key', 'stored-token');
    localStorage.setItem('jarvis_user', 'jeremiah');
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
    expect(localStorage.getItem('jarvis_user')).toBe('jeremiah');
  });

  it('still clears the session when a normal request 401s', async () => {
    server.use(http.get('/api/users/me', () => new HttpResponse(null, { status: 401 })));

    await expect(apiClient.get('/api/users/me')).rejects.toBeTruthy();

    expect(localStorage.getItem('jarvis_api_key')).toBeNull();
    expect(localStorage.getItem('jarvis_user')).toBeNull();
  });
});
