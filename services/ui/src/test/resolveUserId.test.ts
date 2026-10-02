import { describe, it, expect } from 'vitest';
import { resolveUserId } from '../services/api';

/**
 * "all" is the UI's stand-in for "let the server work out who is asking".
 * Forwarding it literally would look up a user named "all", which is why this
 * lived inline at a dozen call sites before being pulled out.
 *
 * Deliberately not mocked: a test that asserts against a copy of the function
 * inside a module mock proves nothing about the real one.
 */
describe('resolveUserId', () => {
  it('drops the "all" sentinel', () => {
    expect(resolveUserId('all')).toBeUndefined();
  });

  it('passes a real username through unchanged', () => {
    expect(resolveUserId('michele')).toBe('michele');
  });

  it('treats a missing or empty user as absent', () => {
    expect(resolveUserId(undefined)).toBeUndefined();
    expect(resolveUserId('')).toBeUndefined();
  });

  it('does not strip a username that merely contains "all"', () => {
    // A substring test would break a legitimate user called "allison".
    expect(resolveUserId('allison')).toBe('allison');
  });
});
