import type { AppVerdict, GuardianApps } from '../../types/api';

const LIST_FIELDS = ['concerns', 'flags', 'permissions', 'devices', 'attention', 'rating_descriptors', 'interactive',
  'review_excerpts', 'security', 'special_access', 'capabilities'] as const;

/**
 * The server omits lists that are empty, and an older report may miss one the
 * card reads. The card indexes them (`.length`, `.map`) during render, where a
 * missing array is not a smaller card but a crash that takes the whole Guardian
 * page with it. Fill them in once, on arrival.
 */
export function normaliseApps(answer: GuardianApps): GuardianApps {
  const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
  return {
    ...answer,
    apps: list(answer.apps).map((app) => {
      const row = app as unknown as Record<string, unknown>;
      const filled: Record<string, unknown> = { ...row };
      for (const field of LIST_FIELDS) filled[field] = list(row[field]);
      return filled as unknown as AppVerdict;
    }),
    devices: list(answer.devices) as GuardianApps['devices'],
    unused: list(answer.unused) as GuardianApps['unused'],
  };
}
