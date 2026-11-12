import { afterEach, describe, expect, it } from 'vitest';
import {
  blockExternalNavigation,
  leavesApp,
  lockedFamilyTabs,
  lockedNav,
  lockedPathAllowed,
  normalizeSections,
  resetLockedModeForTests,
  setLockedMode,
  useLockedMode,
} from './lockedMode';
import { act, renderHook } from '@testing-library/react';

const ALL = normalizeSections(['chat', 'calls', 'devices', 'time', 'chores', 'bible']);

describe('locked mode sections', () => {
  it('keeps chat first, drops unknown ids and repeats, and falls back to chat alone', () => {
    expect(normalizeSections(['bible', 'settings', 'bible', 'chat'])).toEqual(['chat', 'bible']);
    expect(normalizeSections([])).toEqual(['chat']);
    expect(normalizeSections(undefined)).toEqual(['chat']);
  });

  it('offers navigation only to the open sections that have a page', () => {
    expect(lockedNav(ALL).map((s) => s.id)).toEqual(['chat', 'calls', 'devices', 'bible']);
    expect(lockedNav(normalizeSections(['music'])).map((s) => s.to)).toEqual(['/family', '/media']);
  });
});

describe('locked mode routing guard', () => {
  it('always opens the locked home', () => {
    expect(lockedPathAllowed('/', '', ['chat'])).toBe(true);
  });

  it('never opens settings, admin, identity or the lab', () => {
    for (const path of ['/settings', '/admin', '/admin/users', '/identity', '/lab', '/guardian', '/devices', '/notes', '/wander']) {
      expect(lockedPathAllowed(path, '', ALL)).toBe(false);
    }
  });

  it('opens the family chat for chat, and calls only when calls are open', () => {
    expect(lockedPathAllowed('/family', '', ['chat'])).toBe(true);
    expect(lockedPathAllowed('/family', '?tab=chat', ['chat'])).toBe(true);
    expect(lockedPathAllowed('/family', '?tab=calls', ['chat'])).toBe(false);
    expect(lockedPathAllowed('/family', '?tab=calls', ALL)).toBe(true);
    // Games, Create and Announce are not part of any section.
    expect(lockedPathAllowed('/family', '?tab=games', ALL)).toBe(false);
    expect(lockedPathAllowed('/family', '?tab=create', ALL)).toBe(false);
    // A family member's page shows a parent's controls: not open.
    expect(lockedPathAllowed('/family/member/someone', '', ALL)).toBe(false);
  });

  it('opens a section page and its sub-pages only when the section is open', () => {
    expect(lockedPathAllowed('/bible', '?tab=play', ['chat', 'bible'])).toBe(true);
    expect(lockedPathAllowed('/bible', '', ['chat'])).toBe(false);
    expect(lockedPathAllowed('/media/player', '', ['chat', 'music'])).toBe(true);
    expect(lockedPathAllowed('/mediaxyz', '', ['chat', 'music'])).toBe(false);
    expect(lockedPathAllowed('/remote', '', ['chat', 'devices'])).toBe(true);
  });

  it('names the family tabs a locked child may see', () => {
    expect([...lockedFamilyTabs(['chat'])]).toEqual(['chat']);
    expect([...lockedFamilyTabs(ALL)].sort()).toEqual(['calls', 'chat']);
  });
});

describe('links that leave the app', () => {
  const origin = 'https://jarvis.example';

  it('lets in-app links through and stops every other one', () => {
    expect(leavesApp('/bible', null, origin)).toBe(false);
    expect(leavesApp('#top', null, origin)).toBe(false);
    expect(leavesApp('https://jarvis.example/family', null, origin)).toBe(false);
    expect(leavesApp('https://example.org/', null, origin)).toBe(true);
    expect(leavesApp('tel:5551234', null, origin)).toBe(true);
    expect(leavesApp('intent://open#Intent;end', null, origin)).toBe(true);
    expect(leavesApp('/bible', '_blank', origin)).toBe(true);
  });

  it('blocks a click on an outside link and window.open until it is undone', () => {
    const a = document.createElement('a');
    a.href = 'https://example.org/';
    document.body.appendChild(a);
    const realOpen = window.open;
    const undo = blockExternalNavigation();
    const click = new MouseEvent('click', { bubbles: true, cancelable: true });
    a.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true);
    expect(window.open('https://example.org/')).toBeNull();
    undo();
    expect(window.open).toBe(realOpen);
    a.remove();
  });
});

describe('locked mode state', () => {
  afterEach(() => resetLockedModeForTests());

  it('follows what native says', () => {
    const { result } = renderHook(() => useLockedMode());
    expect(result.current.active).toBe(false);
    act(() => setLockedMode({ active: true, sections: ['bible', 'chores'] }));
    expect(result.current).toEqual({ active: true, sections: ['chat', 'bible', 'chores'] });
    act(() => setLockedMode({ active: false, sections: [] }));
    expect(result.current.active).toBe(false);
  });
});
