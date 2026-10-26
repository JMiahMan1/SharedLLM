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

  it('calls the devices page "Shared files" for a member who is not an admin', () => {
    // A parent gets device management; a child gets the files their parents
    // share with them. The same word for both sent a child somewhere they had
    // no reason to look.
    const admin = navPagesFor(true).find((page) => page.path === '/devices');
    const member = navPagesFor(false).find((page) => page.path === '/devices');
    expect(admin?.label).toBe('Devices');
    expect(member?.label).toBe('Shared files');
    expect(member?.path).toBe('/devices');
  });

  it('offers the recipes page, which used to have a route but no way in', () => {
    // It was reachable only through Family -> Create.
    const recipes = NAV_PAGES.find((page) => page.path === '/recipes');
    expect(recipes?.label).toBe('Recipes');
  });
});
