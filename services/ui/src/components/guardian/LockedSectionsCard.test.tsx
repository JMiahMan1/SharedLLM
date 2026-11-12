import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../../test/render';
import LockedSectionsCard from './LockedSectionsCard';
import { api } from '../../services/api';

const CHOICES = [
  { id: 'chat', label: 'Chat with Jarvis and the family', core: true, default: true },
  { id: 'bible', label: 'The Bible', core: false, default: true },
  { id: 'music', label: 'Music', core: false, default: false },
];

describe('While locked, Jarvis can use', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('shows the server choices, keeps chat on, and saves the parents pick with the other rules', async () => {
    vi.spyOn(api, 'getScreenTimeStatus').mockResolvedValue({
      can_manage: true,
      rules: { daily_limit_min: 90, paused: true },
      locked_sections: ['chat', 'bible'],
      locked_section_choices: CHOICES,
    } as unknown as Awaited<ReturnType<typeof api.getScreenTimeStatus>>);
    const save = vi.spyOn(api, 'setScreenRules').mockResolvedValue({ rules: {} });
    const user = userEvent.setup();
    renderWithProviders(<LockedSectionsCard username="kid" name="Kid" />);

    const chat = await screen.findByTestId('locked-section-chat');
    expect(chat).toBeChecked();
    expect(chat).toBeDisabled();
    expect(screen.getByTestId('locked-section-bible')).toBeChecked();
    expect(screen.getByTestId('locked-section-music')).not.toBeChecked();

    await user.click(screen.getByTestId('locked-section-music'));
    await user.click(screen.getByTestId('locked-section-bible'));
    await user.click(screen.getByTestId('locked-sections-save'));
    await waitFor(() => expect(save).toHaveBeenCalled());
    // The other rules go back unchanged; the pause is the server's to keep.
    expect(save).toHaveBeenCalledWith('kid', { daily_limit_min: 90, locked_sections: ['chat', 'music'] });
  });

  it('tells a viewer who cannot change it so', async () => {
    vi.spyOn(api, 'getScreenTimeStatus').mockResolvedValue({
      can_manage: false,
      rules: {},
      locked_sections: ['chat'],
      locked_section_choices: CHOICES,
    } as unknown as Awaited<ReturnType<typeof api.getScreenTimeStatus>>);
    renderWithProviders(<LockedSectionsCard username="kid" name="Kid" />);
    expect(await screen.findByText(/only their parents can change this/i)).toBeInTheDocument();
  });
});
