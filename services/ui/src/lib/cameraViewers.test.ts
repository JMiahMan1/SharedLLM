import { describe, expect, it } from 'vitest';
import { permitListAfter, viewersSummary, type CameraViewer, type CameraViewers } from './cameraViewers';

const person = (username: string, over: Partial<CameraViewer> = {}): CameraViewer => ({
  username, name: username[0].toUpperCase() + username.slice(1), is_admin: false, always: false, can_watch: false, ...over,
});

const PEOPLE = [
  person('parent', { is_admin: true, always: true, can_watch: true }),
  person('teen', { can_watch: true }),
  person('kid'),
  person('sitter', { always: true, can_watch: true }),
];

describe('who can watch', () => {
  it('locks to everyone who watches now, with the one change', () => {
    expect(permitListAfter(PEOPLE, 'kid', true)).toEqual(['kid', 'teen']);
    expect(permitListAfter(PEOPLE, 'teen', false)).toEqual([]);
  });

  it('never puts people who can always watch on the list', () => {
    expect(permitListAfter(PEOPLE, 'kid', true)).not.toContain('parent');
    expect(permitListAfter(PEOPLE, 'kid', true)).not.toContain('sitter');
  });

  it('says how the camera’s viewers are decided', () => {
    const v: CameraViewers = { entity_id: 'camera.shed', limited: false, permitted: [], people: PEOPLE };
    expect(viewersSummary(v)).toMatch(/^3 of 4 people can watch\. Following each person’s device access/);
    expect(viewersSummary({ ...v, limited: true })).toMatch(/Only the people switched on \(and admins\)/);
  });
});
