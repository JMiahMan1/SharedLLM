import { describe, expect, it } from 'vitest';
import {
  buildFamilyLookup,
  familyLookupKeys,
  findFamilyMember,
  type FamilyMember,
} from './familyDirectory';

const member = (over: Partial<FamilyMember> = {}): FamilyMember => ({
  username: 'michele',
  display_name: 'Michele',
  nextcloud_user: 'michele_cloud',
  avatar: 'upload:7',
  ...over,
});

describe('family directory lookup', () => {
  const directory = buildFamilyLookup([
    member(),
    member({ username: 'jeremiah', display_name: 'Jeremiah Summers', nextcloud_user: null, avatar: null }),
  ]);

  it('finds people by SharedLLM username or Nextcloud account', () => {
    expect(findFamilyMember(directory, 'michele')?.username).toBe('michele');
    expect(findFamilyMember(directory, 'MICHELE_CLOUD')?.username).toBe('michele');
  });

  it('finds a direct message by the display name Talk shows when the id is absent', () => {
    expect(findFamilyMember(directory, null, 'Jeremiah Summers')?.username).toBe('jeremiah');
  });

  it('matches a Home Assistant person entity to the account behind it', () => {
    expect(findFamilyMember(directory, 'person.michele_phone', 'Michele')?.username).toBe('michele');
    expect(findFamilyMember(directory, 'user.michele')?.username).toBe('michele');
  });

  it('does not run the match across unrelated words', () => {
    expect(findFamilyMember(directory, 'person.someone_else')).toBeUndefined();
  });

  it('lists exact keys before entity tokens and drops noise words', () => {
    const keys = familyLookupKeys('person.michele_phone', 'Michele');
    expect(keys[0]).toBe('person.michele_phone');
    expect(keys).toContain('michele_phone');
    expect(keys).toContain('michele');
    expect(keys).not.toContain('phone');
  });
});
