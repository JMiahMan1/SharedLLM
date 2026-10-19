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
  username: 'michele',
  display_name: 'Michele',
  nextcloud_user: 'michele',
  avatar: 'upload:42',
  ...over,
});

describe('ConversationAvatar', () => {
  it('gives a direct message the other person’s uploaded picture', () => {
    const people = buildFamilyLookup([member()]);
    render(
      <ConversationAvatar
        conversation={{ type: 1, name: 'michele', display_name: 'Michele' }}
        people={people}
      />,
    );

    const avatar = screen.getByTestId('avatar');
    expect(avatar).toHaveAttribute('data-username', 'michele');
    expect(avatar).toHaveAttribute('data-avatar', 'upload:42');
    expect(avatar).toHaveAttribute('data-name', 'Michele');
  });

  it('matches a direct message that only carries the display name', () => {
    const people = buildFamilyLookup([member()]);
    render(
      <ConversationAvatar
        conversation={{ type: 1, name: undefined, display_name: 'Michele' }}
        people={people}
      />,
    );

    expect(screen.getByTestId('avatar')).toHaveAttribute('data-username', 'michele');
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
