import { describe, expect, it } from 'vitest';
import {
  buildFamilyLookup,
  familyLookupKeys,
  findFamilyMember,
  type FamilyMember,
} from './familyDirectory';

const member = (over: Partial<FamilyMember> = {}): FamilyMember => ({
  username: 'sam',
  display_name: 'Sam',
  nextcloud_user: 'sam_cloud',
  avatar: 'upload:7',
  ...over,
});

describe('family directory lookup', () => {
  const directory = buildFamilyLookup([
    member(),
    member({ username: 'alex', display_name: 'Alex Rivera', nextcloud_user: null, avatar: null }),
  ]);

  it('finds people by SharedLLM username or Nextcloud account', () => {
    expect(findFamilyMember(directory, 'sam')?.username).toBe('sam');
    expect(findFamilyMember(directory, 'SAM_CLOUD')?.username).toBe('sam');
  });

  it('finds a direct message by the display name Talk shows when the id is absent', () => {
    expect(findFamilyMember(directory, null, 'Alex Rivera')?.username).toBe('alex');
  });

  it('matches a Home Assistant person entity to the account behind it', () => {
    expect(findFamilyMember(directory, 'person.sam_phone', 'Sam')?.username).toBe('sam');
    expect(findFamilyMember(directory, 'user.sam')?.username).toBe('sam');
  });

  it('does not run the match across unrelated words', () => {
    expect(findFamilyMember(directory, 'person.someone_else')).toBeUndefined();
  });

  it('lists exact keys before entity tokens and drops noise words', () => {
    const keys = familyLookupKeys('person.sam_phone', 'Sam');
    expect(keys[0]).toBe('person.sam_phone');
    expect(keys).toContain('sam_phone');
    expect(keys).toContain('sam');
    expect(keys).not.toContain('phone');
  });
});
