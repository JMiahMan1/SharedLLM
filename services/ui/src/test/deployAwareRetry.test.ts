/**
 * A deploy is not a lost connection.
 *
 * Each deploy restarts the gateway for 15-25 s and Caddy holds requests while
 * it waits, so the app's 15 s requests time out. Measured on 2026-11-16/17:
 * 23 deploys in 20 hours, 15 of them with a visible stall, and every open page
 * said "Reconnecting to Gateway" and "Cannot reach" each time. These rules keep
 * the app retrying through an update and leave the explaining to its banner.
 */
import { describe, expect, it } from 'vitest';
import {
  RETRY_LIMIT, RETRY_LIMIT_WHILE_UPDATING, isRetryableRequest, isUnreachableFailure, retryDelayMs, retryLimit,
  shouldShowReconnecting, unreachableMessage,
} from '../services/api';

describe('isUnreachableFailure', () => {
  it('is a timeout, a refused or missing network, or a proxy that could not reach the service', () => {
    for (const code of ['ECONNABORTED', 'ERR_NETWORK', 'ECONNREFUSED', 'ENOTFOUND']) {
      expect(isUnreachableFailure(undefined, code)).toBe(true);
    }
    for (const status of [502, 503, 504]) expect(isUnreachableFailure(status, undefined)).toBe(true);
  });

  it('is not an answer the server gave', () => {
    for (const status of [400, 401, 403, 404, 500]) expect(isUnreachableFailure(status, undefined)).toBe(false);
    expect(isUnreachableFailure(undefined, 'ERR_BAD_REQUEST')).toBe(false);
  });
});

describe('retrying through an update', () => {
  it('does not replay a 502: the gateway uses it for a backing service that answered badly', () => {
    expect(isRetryableRequest('GET', 502, undefined)).toBe(false);
    expect(isRetryableRequest('GET', 504, undefined)).toBe(true);
  });

  it('keeps trying for longer while Jarvis is updating', () => {
    expect(retryLimit(false)).toBe(RETRY_LIMIT);
    expect(retryLimit(true)).toBe(RETRY_LIMIT_WHILE_UPDATING);
    const noJitter = () => 0;
    let total = 0;
    for (let attempt = 0; attempt < RETRY_LIMIT_WHILE_UPDATING; attempt += 1) total += retryDelayMs(attempt, noJitter);
    // Longer than the slowest restart measured (26 s), short of forever.
    expect(total).toBeGreaterThan(30_000);
    expect(total).toBeLessThan(90_000);
  });

  it('backs off 1, 2, 4 s, then never waits more than 8 s (plus jitter)', () => {
    const noJitter = () => 0;
    expect([0, 1, 2, 3, 6].map((a) => retryDelayMs(a, noJitter))).toEqual([1000, 2000, 4000, 8000, 8000]);
    expect(retryDelayMs(0, () => 0.999)).toBeLessThan(2000);
  });
});

describe('what the person is told', () => {
  it('says nothing on a first retry: one slow answer a retry fixes is not a connection problem', () => {
    expect(shouldShowReconnecting(0, false)).toBe(false);
    expect(shouldShowReconnecting(1, false)).toBe(true);
    expect(shouldShowReconnecting(2, false)).toBe(true);
  });

  it('never shows "Reconnecting" during an update; the update banner says it', () => {
    for (let attempt = 0; attempt < RETRY_LIMIT_WHILE_UPDATING; attempt += 1) {
      expect(shouldShowReconnecting(attempt, true)).toBe(false);
    }
  });

  it('blames the update, not the network, for a request that failed during one', () => {
    expect(unreachableMessage('Gateway', true)).toMatch(/updating/);
    expect(unreachableMessage('Gateway', true)).not.toMatch(/Cannot reach|network/);
    expect(unreachableMessage('Gateway', false)).toMatch(/Cannot reach Gateway/);
  });
});
