import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Family from '../pages/Family';
import { renderWithProviders } from './render';

describe('Family hub', () => {
  beforeEach(() => localStorage.clear());

  it('opens on chat with Nextcloud Talk conversations', async () => {
    renderWithProviders(<Family />);
    expect(await screen.findByTestId('chat-panel')).toBeInTheDocument();

    // Seeded fixtures: the Family room's latest message shows in the feed
    await waitFor(() =>
      expect(screen.getByTestId('chat-feed')).toHaveTextContent('Dinner at 6.')
    );
  });

  it('switches to Games and Create tabs', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Family />);

    await user.click(screen.getByRole('tab', { name: /games/i }));
    expect(await screen.findByTestId('family-games')).toHaveTextContent(/bible trivia/i);

    await user.click(screen.getByRole('tab', { name: /create/i }));
    expect(await screen.findByTestId('family-create')).toHaveTextContent(/shared family space/i);
  });

  it('opens a conversation from a username and sends a message', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Family />);

    await user.type(await screen.findByLabelText('Start a conversation with'), 'jeremiah');
    await user.click(screen.getByRole('button', { name: /open conversation/i }));
    await waitFor(() => expect(screen.getByTestId('chat-feed')).toBeInTheDocument());
  });

  it('records a voice message, previews it, then sends with a caption', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Family />);

    await user.click(await screen.findByRole('button', { name: /record a voice message/i }));
    await user.click(screen.getByRole('button', { name: /stop recording/i }));

    expect(await screen.findByTestId('voice-preview')).toHaveTextContent(/recorded clip ready/i);
    await user.type(screen.getByLabelText('Voice message caption'), 'Voice update');
    await user.click(screen.getByRole('button', { name: /send voice/i }));

    await waitFor(() => expect(screen.queryByTestId('voice-preview')).not.toBeInTheDocument());
  });

  it('reacts to a message from the quick reaction bar', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Family />);

    await waitFor(() => expect(screen.getByTestId('chat-feed')).toHaveTextContent('Dinner at 6.'));
    await user.click(await screen.findByTitle('Tap to react'));

    const bar = await screen.findByTestId('reaction-bar');
    expect(bar).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /react \u2764\ufe0f/i }));

    expect(await screen.findByTestId('reaction-chips')).toHaveTextContent('❤️');
  });

  it('shows family polls and records a vote', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Family />);

    const list = await screen.findByTestId('poll-list');
    expect(list).toHaveTextContent('Dinner tonight?');
    await user.click(screen.getByRole('button', { name: /tacos/i }));

    await waitFor(() => expect(screen.getByTestId('poll-list')).toHaveTextContent('2'));
  });

  it('creates a poll from the poll form', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Family />);

    await user.click(await screen.findByRole('button', { name: /new poll/i }));
    await user.type(await screen.findByLabelText('Poll question'), 'Movie night?');
    await user.type(screen.getByLabelText('Poll option 1'), 'Inside Out');
    await user.type(screen.getByLabelText('Poll option 2'), 'Paddington');
    await user.click(screen.getByRole('button', { name: /post poll/i }));

    await waitFor(() => expect(screen.getByTestId('poll-list')).toHaveTextContent('Movie night?'));
  });

  it('sends a chat message from the composer', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Family />);

    const input = await screen.findByLabelText('Message');
    await user.type(input, 'Dinner at six?');
    await user.click(screen.getByRole('button', { name: /send message/i }));

    await waitFor(() => expect(input).toHaveValue(''));
  });
});
