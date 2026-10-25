import { describe, it, expect } from 'vitest';
import { NAV_PAGES, navPagesFor } from './navItems';

describe('nav pages', () => {
  it('offers the family guardian as its own page, right after Family', () => {
    const index = NAV_PAGES.findIndex((page) => page.path === '/guardian');
    const family = NAV_PAGES.findIndex((page) => page.path === '/family');
    expect(index).toBeGreaterThan(family);
    expect(NAV_PAGES[index]).toMatchObject({ label: 'Guardian', path: '/guardian' });
  });

  it('shows the guardian page to every role', () => {
    expect(NAV_PAGES.find((page) => page.path === '/guardian')?.adminOnly).toBeUndefined();
    expect(navPagesFor(false).some((page) => page.path === '/guardian')).toBe(true);
  });

  it('does not pin the guardian page to the phone bottom bar', () => {
    expect(NAV_PAGES.find((page) => page.path === '/guardian')?.pinnedOnMobile).toBeUndefined();
  });
});
