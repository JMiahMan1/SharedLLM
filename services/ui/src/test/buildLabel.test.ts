import { describe, it, expect, vi } from 'vitest';

vi.mock('@capacitor/core', () => ({
  Capacitor: { isNativePlatform: () => false, getPlatform: () => 'web' },
  registerPlugin: () => ({}),
}));

import { buildLabel } from '../lib/appUpdater';

describe('buildLabel', () => {
  it('names the version, the build number and the commit', () => {
    expect(buildLabel('1.7.1', 812, '39a8de3bc5f1')).toBe('v1.7.1 · build 812 · 39a8de3bc');
  });

  it('leaves out what is not known', () => {
    expect(buildLabel('1.7.1', undefined, 'unknown')).toBe('v1.7.1');
    expect(buildLabel(undefined, 799, undefined)).toBe('build 799');
    expect(buildLabel('', -1, '')).toBe('…');
  });

  it('does not double a leading v', () => {
    expect(buildLabel('v1.7.1')).toBe('v1.7.1');
  });
});
