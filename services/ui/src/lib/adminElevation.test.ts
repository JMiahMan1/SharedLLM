import { beforeEach, describe, expect, it } from 'vitest';
import { ADMIN_ELEVATION_MS, adminElevated, markAdminElevated } from './adminElevation';

describe('admin elevation', () => {
  beforeEach(() => window.sessionStorage.clear());

  it('holds for fifteen minutes after a passed check, then asks again', () => {
    expect(adminElevated(1_000)).toBe(false);
    markAdminElevated(1_000);
    expect(adminElevated(1_000 + ADMIN_ELEVATION_MS - 1)).toBe(true);
    expect(adminElevated(1_000 + ADMIN_ELEVATION_MS + 1)).toBe(false);
  });

  it('is kept in session storage, so a reload keeps it but a new app session does not', () => {
    markAdminElevated(5_000);
    expect(window.sessionStorage.getItem('jarvis_admin_elevated_until')).toBe(String(5_000 + ADMIN_ELEVATION_MS));
    expect(window.localStorage.getItem('jarvis_admin_elevated_until')).toBeNull();
  });
});
