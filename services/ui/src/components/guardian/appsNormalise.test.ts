import { describe, expect, it } from 'vitest';
import { normaliseApps } from './AppsCard';
import { APPS } from '../../test/appVettingFixtures';
import type { GuardianApps } from '../../types/api';

/**
 * A report from an older agent (or a partial answer) omits the lists that were
 * empty. `AppDetail` reads them with `.length` and `.map` during render, so a
 * missing array was not a smaller card: it threw and the route's error boundary
 * replaced the whole Guardian page with "Something went wrong".
 */
describe('normaliseApps', () => {
  it('fills in every list the card reads', () => {
    const sparse = structuredClone(APPS) as unknown as GuardianApps;
    const app = sparse.apps[0] as unknown as Record<string, unknown>;
    for (const field of ['concerns', 'permissions', 'attention', 'flags', 'security', 'capabilities']) delete app[field];
    delete (sparse as unknown as Record<string, unknown>).unused;
    delete (sparse as unknown as Record<string, unknown>).devices;

    const filled = normaliseApps(sparse);
    const first = filled.apps[0] as unknown as Record<string, unknown>;

    for (const field of ['concerns', 'permissions', 'attention', 'flags', 'security', 'capabilities']) {
      expect(Array.isArray(first[field]), field).toBe(true);
    }
    expect(filled.unused).toEqual([]);
    expect(filled.devices).toEqual([]);
  });

  it('keeps what the server did send, rather than replacing it', () => {
    const answer = structuredClone(APPS) as unknown as GuardianApps;
    const filled = normaliseApps(answer);

    expect(filled.apps.length).toBe(answer.apps.length);
    expect(filled.apps[0].label).toBe(answer.apps[0].label);
    expect(filled.apps[0].concerns).toEqual(answer.apps[0].concerns);
    expect(filled.policy).toEqual(answer.policy);
  });
});
