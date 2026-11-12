import { describe, it, expect } from 'vitest';
import { NAV_GROUPS, NAV_PAGES, navPagesFor, navSectionsFor, sectionOfPath } from './navItems';

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

  describe('sections', () => {
    const labelsIn = (isAdmin: boolean, key: string) =>
      navSectionsFor(isAdmin).find((section) => section.key === key)?.pages.map((page) => page.label) ?? [];

    it('puts every page in a known section', () => {
      const keys = new Set(NAV_GROUPS.map((group) => group.key));
      for (const page of NAV_PAGES) expect(keys.has(page.group)).toBe(true);
    });

    it('groups the everyday home controls under Home', () => {
      expect(labelsIn(true, 'home')).toEqual(['Rooms', 'Climate', 'Cameras', 'Energy', 'Remote']);
    });

    it('groups setting up the home under Manage home, with Devices for an admin', () => {
      expect(labelsIn(true, 'manage')).toEqual(['Schedules', 'Automations', 'Devices', 'History']);
    });

    it('files a member\'s "Shared files" with the other files, not with home setup', () => {
      expect(labelsIn(false, 'manage')).not.toContain('Shared files');
      expect(labelsIn(false, 'library')).toContain('Shared files');
    });

    it('hides admin-only pages from a member and keeps them for an admin', () => {
      expect(labelsIn(false, 'system')).toEqual(['Settings']);
      expect(labelsIn(true, 'system')).toEqual(expect.arrayContaining(['Lab', 'Admin', 'Settings']));
    });

    it('lists every page this role may open exactly once across the sections', () => {
      for (const isAdmin of [true, false]) {
        const inSections = navSectionsFor(isAdmin).flatMap((section) => section.pages.map((page) => page.path));
        expect(inSections.sort()).toEqual(navPagesFor(isAdmin).map((page) => page.path).sort());
      }
    });

    it('drops a section with nothing in it for the pages given', () => {
      const onlyRooms = navPagesFor(true).filter((page) => page.path === '/rooms');
      expect(navSectionsFor(true, onlyRooms).map((section) => section.key)).toEqual(['home']);
    });

    it('finds the section of a page and of its child routes', () => {
      const sections = navSectionsFor(true);
      expect(sectionOfPath(sections, '/climate')).toBe('home');
      expect(sectionOfPath(sections, '/media/library')).toBe('everyday');
      expect(sectionOfPath(sections, '/')).toBe('everyday');
      expect(sectionOfPath(sections, '/nowhere')).toBeNull();
    });

    it('keeps the phone bottom bar at five pinned pages', () => {
      expect(NAV_PAGES.filter((page) => page.pinnedOnMobile !== undefined).length).toBeLessThanOrEqual(5);
    });
  });
});
