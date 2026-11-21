/**
 * Why a device was signed out: the notice helper, and the api client's 401
 * handling that records it. A reset sign-in (401 with code "sign_in_reset")
 * and any other refused key must both clear the stored key and say why; a
 * 503 is "try again", never a sign-out.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { AxiosError, AxiosHeaders } from 'axios';
import { server } from './setup';
import { apiClient, resetSignOutLatchForTests } from '../services/api';
import {
  SESSION_ENDED_MESSAGE,
  SIGN_IN_RESET_MESSAGE,
  SIGNOUT_REASON_KEY,
  carriedKey,
  clearSignOutReason,
  isSignInReset,
  readSignOutMessage,
  readSignOutReason,
  recordSignOutReason,
  signOutReasonFor,
} from '../lib/signOutNotice';

const RESET_BODY = { detail: 'Your sign-in was reset, please sign in again.', code: 'sign_in_reset' };

function axiosErrorWith(status: number, data: unknown, auth?: string): AxiosError {
  const headers = new AxiosHeaders();
  if (auth) headers.set('Authorization', auth);
  const config = { headers } as never;
  return new AxiosError('failed', 'ERR_BAD_REQUEST', config, undefined, {
    status, statusText: '', headers: {}, config, data,
  } as never);
}

describe('sign-out notice helper', () => {
  afterEach(() => localStorage.clear());

  it('recognises a reset sign-in only from a 401 carrying the code', () => {
    expect(isSignInReset(axiosErrorWith(401, RESET_BODY))).toBe(true);
    expect(isSignInReset(axiosErrorWith(401, { detail: 'Invalid API key' }))).toBe(false);
    expect(isSignInReset(axiosErrorWith(403, RESET_BODY))).toBe(false);
    expect(isSignInReset(new Error('network'))).toBe(false);
    expect(isSignInReset(null)).toBe(false);
  });

  it('picks the reason for a refused key', () => {
    expect(signOutReasonFor(axiosErrorWith(401, RESET_BODY))).toBe('sign_in_reset');
    expect(signOutReasonFor(axiosErrorWith(401, { detail: 'Invalid API key' }))).toBe('session_ended');
  });

  it('knows whether the request carried a key', () => {
    expect(carriedKey(axiosErrorWith(401, {}, 'Bearer fake-key-123'))).toBe(true);
    expect(carriedKey(axiosErrorWith(401, {}))).toBe(false);
  });

  it('records, reads and clears the reason, and ignores anything it did not write', () => {
    expect(readSignOutReason()).toBeNull();
    recordSignOutReason('sign_in_reset');
    expect(readSignOutMessage()).toBe(SIGN_IN_RESET_MESSAGE);
    recordSignOutReason('session_ended');
    expect(readSignOutMessage()).toBe(SESSION_ENDED_MESSAGE);
    clearSignOutReason();
    expect(readSignOutMessage()).toBeNull();
    localStorage.setItem(SIGNOUT_REASON_KEY, 'something-else');
    expect(readSignOutReason()).toBeNull();
  });
});

describe('api client 401 handling', () => {
  beforeEach(() => {
    resetSignOutLatchForTests();
    localStorage.setItem('jarvis_api_key', 'fake-key-123');
    localStorage.setItem('jarvis_user', JSON.stringify({ username: 'parent' }));
  });

  afterEach(() => {
    localStorage.clear();
    resetSignOutLatchForTests();
  });

  it('clears the stored key and records a reset sign-in', async () => {
    server.use(http.get('/api/users/me', () => HttpResponse.json(RESET_BODY, { status: 401 })));
    await expect(apiClient.get('/api/users/me')).rejects.toBeTruthy();
    expect(localStorage.getItem('jarvis_api_key')).toBeNull();
    expect(localStorage.getItem('jarvis_user')).toBeNull();
    expect(readSignOutReason()).toBe('sign_in_reset');
  });

  it('records a plain refused key as a session that ended', async () => {
    server.use(http.get('/api/chores', () => HttpResponse.json({ detail: 'Invalid API key' }, { status: 401 })));
    await expect(apiClient.get('/api/chores')).rejects.toBeTruthy();
    expect(localStorage.getItem('jarvis_api_key')).toBeNull();
    expect(readSignOutReason()).toBe('session_ended');
  });

  it('a 503 is not a sign-out', async () => {
    server.use(http.post('/api/chores', () => HttpResponse.json({ detail: 'Try again' }, { status: 503 })));
    await expect(apiClient.post('/api/chores', {}, { silent: true })).rejects.toBeTruthy();
    expect(localStorage.getItem('jarvis_api_key')).toBe('fake-key-123');
    expect(readSignOutReason()).toBeNull();
  });

  it('an optional call and the login request keep the session and record nothing', async () => {
    server.use(
      http.get('/api/users/me/theme', () => HttpResponse.json(RESET_BODY, { status: 401 })),
      http.post('/api/auth/login', () => HttpResponse.json({ detail: 'Invalid username or password' }, { status: 401 })),
    );
    await expect(apiClient.get('/api/users/me/theme', { skipAuthRedirect: true })).rejects.toBeTruthy();
    await expect(apiClient.post('/api/auth/login', { username: 'parent', password: 'fake-password' })).rejects.toBeTruthy();
    expect(localStorage.getItem('jarvis_api_key')).toBe('fake-key-123');
    expect(readSignOutReason()).toBeNull();
  });

  it('a 401 on a request that carried no key records no reason', async () => {
    localStorage.removeItem('jarvis_api_key');
    server.use(http.get('/api/chores', () => HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })));
    await expect(apiClient.get('/api/chores')).rejects.toBeTruthy();
    expect(readSignOutReason()).toBeNull();
  });
});
