import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import UserAvatar from './UserAvatar';
import AvatarPicker from './AvatarPicker';
import { AVATAR_PRESETS, presetFor } from '../lib/avatarPresets';

const apiMock = vi.hoisted(() => ({
  getAvatarImage: vi.fn(),
  chooseAvatar: vi.fn(),
  uploadAvatar: vi.fn(),
  removeAvatar: vi.fn(),
}));
vi.mock('../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../services/api')>()).api, ...apiMock },
}));

const wrap = (ui: React.ReactNode) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);

beforeEach(() => vi.clearAllMocks());

describe('profile pictures', () => {
  it('has a good handful of distinct Bible characters with custom SVG artwork', () => {
    expect(AVATAR_PRESETS.length).toBeGreaterThanOrEqual(26);
    expect(new Set(AVATAR_PRESETS.map((p) => p.id)).size).toBe(AVATAR_PRESETS.length);
    expect(new Set(AVATAR_PRESETS.map((p) => p.svg.trim())).size).toBe(AVATAR_PRESETS.length);
    for (const preset of AVATAR_PRESETS) {
      expect(preset.svg.trim().length).toBeGreaterThan(100);
    }
    expect(presetFor('preset:balaams-donkey')?.emoji).toBe('🫏');
    expect(presetFor('upload:1')).toBeNull();
  });

  it('draws a preset, initials, or the uploaded photo', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:photo');
    URL.revokeObjectURL = vi.fn();
    apiMock.getAvatarImage.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    wrap(
      <>
        <UserAvatar username="mia" avatar="preset:jonahs-whale" />
        <UserAvatar username="sam" name="Sam Summers" />
        <UserAvatar username="ben" avatar="upload:123" />
      </>,
    );
    const presetAvatar = screen.getByTestId('user-avatar-preset');
    expect(presetAvatar).toHaveAccessibleName("mia: Jonah's Whale");
    expect(presetAvatar.querySelector('svg')).not.toBeNull();
    expect(screen.getByRole('img', { name: 'Sam Summers' })).toHaveTextContent('SS');
    expect(await screen.findByTestId('user-avatar-photo')).toHaveAttribute('src', 'blob:photo');
    expect(apiMock.getAvatarImage).toHaveBeenCalledWith('ben');
  });

  it('saves a chosen character', async () => {
    const user = userEvent.setup();
    apiMock.chooseAvatar.mockResolvedValue({ username: 'mia', avatar: 'preset:zacchaeus' });
    wrap(<AvatarPicker user={{ id: 2, username: 'mia', is_admin: false } as never} onClose={vi.fn()} />);
    await user.click(screen.getByTestId('avatar-preset-zacchaeus'));
    await waitFor(() => expect(apiMock.chooseAvatar).toHaveBeenCalledWith('zacchaeus'));
  });
});
