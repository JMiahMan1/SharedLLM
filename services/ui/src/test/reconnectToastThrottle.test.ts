/**
 * The recovery toast is throttled.
 *
 * A page with several polling queries has many requests in flight at once, and
 * each retries independently. Without a throttle, one network blip is announced
 * once per in-flight request -- the toast spam reported against the browser
 * build. The error toast was already throttled; the success one was not.
 *
 * The decision is tested directly rather than through axios's interceptor
 * machinery, which would need a callable mocked default export, a captured
 * rejection handler and fake backoff timers to assert one comparison.
 */
import { describe, it, expect } from 'vitest';
import { shouldAnnounceReconnect } from '../services/api';

const WINDOW = 15000;

describe('shouldAnnounceReconnect', () => {
  it('announces the first recovery, since nothing has been announced yet', () => {
    // lastAnnounced starts at 0, so the first call is always outside the window.
    expect(shouldAnnounceReconnect(WINDOW + 1, 0)).toBe(true);
  });

  it('suppresses a second recovery in the same instant', () => {
    // Four polling queries all recover within the same second; only the first
    // should be reported.
    expect(shouldAnnounceReconnect(50_000, 50_000)).toBe(false);
  });

  it('suppresses recoveries inside the window', () => {
    expect(shouldAnnounceReconnect(10_000, 0)).toBe(false);
    expect(shouldAnnounceReconnect(WINDOW, 0)).toBe(false);
  });

  it('announces again once the window has passed', () => {
    // A genuinely separate outage later on is worth telling the user about.
    expect(shouldAnnounceReconnect(WINDOW + 1, 0)).toBe(true);
    expect(shouldAnnounceReconnect(40_000, 20_000)).toBe(true);
  });

  it('honours a custom window', () => {
    expect(shouldAnnounceReconnect(2_000, 0, 1_000)).toBe(true);
    expect(shouldAnnounceReconnect(500, 0, 1_000)).toBe(false);
  });

  it('collapses a burst to a single announcement', () => {
    // The real scenario: N requests recover within the same window.
    const recoveries = [20_000, 20_010, 20_050, 20_100, 20_400, 21_000, 25_000];
    let last = 0;
    let announced = 0;
    for (const now of recoveries) {
      if (shouldAnnounceReconnect(now, last)) {
        announced += 1;
        last = now;
      }
    }
    expect(announced).toBe(1);
  });

  it('announces once per window across a long outage', () => {
    let last = 0;
    let announced = 0;
    for (let i = 1; i <= 60; i += 1) {
      const now = i * 60_000; // one recovery a minute for an hour
      if (shouldAnnounceReconnect(now, last)) {
        announced += 1;
        last = now;
      }
    }
    expect(announced).toBe(60);
  });
});
