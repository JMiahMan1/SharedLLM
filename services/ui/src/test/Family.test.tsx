import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import Family from '../pages/Family';
import { renderWithProviders } from './render';
import { server } from './setup';

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

  it('opens the tab a notice links to, with On my way and SOS beside the title', async () => {
    renderWithProviders(<Family />, { route: '/family?tab=create' });
    expect(await screen.findByTestId('family-create')).toBeInTheDocument();
    const safety = screen.getByTestId('family-safety');
    expect(within(safety).getByRole('button', { name: /on my way/i })).toBeInTheDocument();
    expect(within(safety).getByRole('button', { name: /^sos$/i })).toBeInTheDocument();
  });

  it('switches to Games and Create tabs', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Family />);

    await user.click(screen.getByRole('tab', { name: /games/i }));
    const games = await screen.findByTestId('family-games');
    // Real games only: each links to where it is played, nothing marked "planned".
    expect(within(games).getByRole('link', { name: /daily bible quiz/i })).toHaveAttribute('href', '/bible?tab=play');
    expect(within(games).getByRole('link', { name: /memory verses/i })).toHaveAttribute('href', '/bible?tab=memorize');
    expect(games).not.toHaveTextContent(/planned/i);

    await user.click(screen.getByRole('tab', { name: /create/i }));
    const create = await screen.findByTestId('family-create');
    expect(create).toHaveTextContent(/sent straight into a conversation/i);
    // What the copy promises is there: a drawing goes to a chat from the canvas.
    expect(within(create).getAllByRole('button', { name: /send to chat/i }).length).toBeGreaterThan(0);
  });

  it('sends a drawing into a conversation the person picks', async () => {
    const user = userEvent.setup();
    const sent: Array<{ token?: string; mime_type?: string; file_name?: string }> = [];
    server.use(
      http.post('/api/communication/talk/file', async ({ request }) => {
        sent.push((await request.json()) as never);
        return HttpResponse.json({ status: 'SUCCESS', message: 'Sent' });
      }),
    );
    // jsdom has no canvas: stand in a PNG export and a 2D context so a stroke counts.
    const toDataURL = vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/png;base64,QUJD');
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, fillRect() {}, clearRect() {}, drawImage() {},
    } as never);
    renderWithProviders(<Family />);

    await user.click(screen.getByRole('tab', { name: /create/i }));
    const surface = await screen.findByTestId('draw-surface');
    fireEvent.pointerDown(surface, { clientX: 5, clientY: 5, pointerId: 1 });
    fireEvent.pointerUp(surface, { pointerId: 1 });
    await user.click(within(screen.getByTestId('draw-canvas')).getByRole('button', { name: /send to chat/i }));

    const sheet = await screen.findByTestId('send-to-chat');
    await user.click(await within(sheet).findByRole('button', { name: /^family$/i }));

    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ token: expect.any(String), file_base64: 'QUJD', mime_type: 'image/png', file_name: 'drawing.png' });
    toDataURL.mockRestore();
    getContext.mockRestore();
  });

  it('starts a direct message by picking a person', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Family />);

    await user.click(await screen.findByRole('button', { name: /new conversation/i }));
    await user.type(await screen.findByLabelText('Search people'), 'jer');
    await user.click(await screen.findByRole('button', { name: /Jeremiah/ }));
    await user.click(screen.getByTestId('new-chat-start'));
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
    // The poll is pinned as one line; open it to vote.
    await user.click(within(list).getByRole('button', { name: /dinner tonight/i }));
    await user.click(screen.getByRole('button', { name: /tacos/i }));

    await waitFor(() => expect(screen.getByTestId('poll-list')).toHaveTextContent('2'));
  });

  it('creates a poll from the poll form', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Family />);

    // Polls live in the composer's + menu, as in WhatsApp and Telegram.
    await user.click(await screen.findByRole('button', { name: /more options/i }));
    await user.click(await screen.findByRole('menuitem', { name: /new poll/i }));
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
