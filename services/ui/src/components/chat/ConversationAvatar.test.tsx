import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ConversationAvatar from './ConversationAvatar';
import { buildFamilyLookup, type FamilyMember } from './familyDirectory';

vi.mock('../UserAvatar', () => ({
  default: ({ username, avatar, name, size }: {
    username: string;
    avatar?: string | null;
    name?: string;
    size?: number;
  }) => (
    <span
      data-testid="avatar"
      data-username={username}
      data-avatar={avatar ?? ''}
      data-name={name}
      data-size={size}
    />
  ),
}));

const member = (over: Partial<FamilyMember> = {}): FamilyMember => ({
  username: 'sam',
  display_name: 'Sam',
  nextcloud_user: 'sam',
  avatar: 'upload:42',
  ...over,
});

describe('ConversationAvatar', () => {
  it('gives a direct message the other person’s uploaded picture', () => {
    const people = buildFamilyLookup([member()]);
    render(
      <ConversationAvatar
        conversation={{ type: 1, name: 'sam', display_name: 'Sam' }}
        people={people}
      />,
    );

    const avatar = screen.getByTestId('avatar');
    expect(avatar).toHaveAttribute('data-username', 'sam');
    expect(avatar).toHaveAttribute('data-avatar', 'upload:42');
    expect(avatar).toHaveAttribute('data-name', 'Sam');
  });

  it('matches a direct message that only carries the display name', () => {
    const people = buildFamilyLookup([member()]);
    render(
      <ConversationAvatar
        conversation={{ type: 1, name: undefined, display_name: 'Sam' }}
        people={people}
      />,
    );

    expect(screen.getByTestId('avatar')).toHaveAttribute('data-username', 'sam');
  });

  it('leaves UserAvatar to draw initials when the person is not in the directory', () => {
    render(
      <ConversationAvatar
        conversation={{ type: 1, name: 'stranger', display_name: 'Stranger' }}
        people={buildFamilyLookup([])}
      />,
    );

    expect(screen.getByTestId('avatar')).toHaveAttribute('data-username', 'stranger');
    expect(screen.getByTestId('avatar')).toHaveAttribute('data-avatar', '');
  });
});
