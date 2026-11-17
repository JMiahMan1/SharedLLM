import { describe, expect, it } from 'vitest';
import { FAILURES_BEFORE_UNREACHABLE, showUnreachable } from './connectivity';

/**
 * "Server unreachable" (with a box to change the server address) used to show
 * after one failed /health check. A deploy holds that check past its 10 s
 * timeout, and a phone just back from sleep can miss one too.
 */
describe('showUnreachable', () => {
  it('waits for a second failed check in a row', () => {
    expect(FAILURES_BEFORE_UNREACHABLE).toBe(2);
    expect(showUnreachable(0, false)).toBe(false);
    expect(showUnreachable(1, false)).toBe(false);
    expect(showUnreachable(2, false)).toBe(true);
    expect(showUnreachable(5, false)).toBe(true);
  });

  it('never calls an update an unreachable server', () => {
    expect(showUnreachable(2, true)).toBe(false);
    expect(showUnreachable(10, true)).toBe(false);
  });
});
