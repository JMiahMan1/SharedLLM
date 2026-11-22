import { describe, expect, it } from 'vitest';
import {
  FOCUS_NOUNS,
  NOTICE_ROUTES,
  focusNoun,
  focusSelector,
  linkHref,
  noticeHref,
  routeOf,
} from './noticeLink';

describe('notice links (docs/PUSH.md, Deep links)', () => {
  it('turns a link into the address of the page and the item', () => {
    expect(linkHref({ route: 'member', path: '/family/member/sam', query: { section: 'time' }, focus: 'unlock-21' }))
      .toBe('/family/member/sam?section=time&focus=unlock-21');
    expect(linkHref({ route: 'chat', path: '/family', query: { chat: 'abc' }, focus: 'msg-42' }))
      .toBe('/family?chat=abc&focus=msg-42');
    expect(linkHref({ route: 'admin', path: '/admin/ops', query: { mission: '7' } })).toBe('/admin/ops?mission=7');
    expect(linkHref({ route: 'devices', path: '/devices', hash: 'add', focus: 'discovery-ab12' }))
      .toBe('/devices?focus=discovery-ab12#add');
  });

  it('opens a Bible reminder on the chapter at its verse', () => {
    expect(linkHref({ route: 'bible', path: '/bible', query: { ref: 'John 3', verse: '16' }, hash: 'v16' }))
      .toBe('/bible?ref=John+3&verse=16#v16');
    expect(linkHref({ route: 'bible', path: '/bible', query: { ref: 'John 3' }, hash: 'chapter' })).toBeNull();
  });

  it('refuses a link no page of this app reads', () => {
    expect(linkHref(null)).toBeNull();
    expect(linkHref({ route: 'nowhere', path: '/x' })).toBeNull();
    expect(linkHref({ route: 'member', path: '/admin/ops' })).toBeNull();
    expect(linkHref({ route: 'member', path: '//evil.example/x' })).toBeNull();
    expect(linkHref({ route: 'member', path: '/family/member/sam', query: { colour: 'red' } })).toBeNull();
    expect(linkHref({ route: 'member', path: '/family/member/sam', query: { section: 'secrets' } })).toBeNull();
    expect(linkHref({ route: 'member', path: '/family/member/sam', focus: 'plant-3' })).toBeNull();
    expect(linkHref({ route: 'admin', path: '/admin/root' })).toBeNull();
    expect(linkHref({ route: 'devices', path: '/devices', hash: 'nope' })).toBeNull();
  });

  it('follows the link first, then the older in-app url', () => {
    expect(noticeHref({ link: { route: 'garden', path: '/garden', focus: 'plant-4' }, url: '/garden' }))
      .toBe('/garden?focus=plant-4');
    // A newer server's route this build does not know: the url it also sends.
    expect(noticeHref({ link: { route: 'future', path: '/future' }, url: '/family?chat=t' })).toBe('/family?chat=t');
    expect(noticeHref({ url: '/wander?person=leo' })).toBe('/wander?person=leo');
    expect(noticeHref({ url: 'https://example.com' })).toBeNull();
    expect(noticeHref({ url: '//example.com' })).toBeNull();
    expect(noticeHref(undefined)).toBeNull();
  });

  it('knows which guard an address is under', () => {
    expect(routeOf('/admin/ops?mission=4')).toEqual({ route: 'admin', guard: 'admin' });
    expect(routeOf('/family?chat=t&beloved=1')).toEqual({ route: 'chat', guard: 'beloved' });
    expect(routeOf('/family?chat=t&private=1&focus=msg-3')).toEqual({ route: 'chat', guard: 'private_chat' });
    expect(routeOf('/family/member/sam?focus=unlock-2')).toEqual({ route: 'member', guard: 'parent' });
    expect(routeOf('/family')).toEqual({ route: 'family', guard: 'member' });
    expect(routeOf('/nowhere')).toBeNull();
  });

  it('has a word for every kind of item a page can show', () => {
    for (const [name, spec] of Object.entries(NOTICE_ROUTES)) {
      for (const kind of spec.focus) expect(FOCUS_NOUNS[kind], `${name} ${kind}`).toBeTruthy();
    }
    expect(focusNoun('msg-42')).toBe('message');
    expect(focusNoun('odd')).toBe('item');
  });

  it('selects the marked element safely', () => {
    expect(focusSelector('app-com.example.game')).toContain('data-focus-id');
    document.body.innerHTML = '<div data-focus-id="app-com.example.game"></div>';
    expect(document.querySelector(focusSelector('app-com.example.game'))).not.toBeNull();
  });
});
