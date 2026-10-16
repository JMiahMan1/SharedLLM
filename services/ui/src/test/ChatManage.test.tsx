import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import Family from '../pages/Family';
import { renderWithProviders } from './render';
import { server } from './setup';

const ok = (detail: Record<string, unknown> = {}, message = 'ok') => HttpResponse.json({ status: 'SUCCESS', message, service: 'talk', detail });

describe('managing family conversations', () => {
  it('opens straight into the conversation set to open on launch', async () => {
    server.use(http.get('/api/users/me', () => HttpResponse.json({ id: 1, username: 'default', is_admin: true, chat_default_token: 'room-work' })));
    renderWithProviders(<Family />);
    const thread = await screen.findByTestId('chat-thread');
    await waitFor(() => expect(within(thread).getByText('Ops')).toBeInTheDocument());
    expect(screen.getByLabelText('Opens on launch')).toBeInTheDocument();
  });

  it('creates a named group from several people', async () => {
    const user = userEvent.setup();
    let created: Record<string, unknown> | null = null;
    server.use(
      http.post('/api/communication/talk/conversations/group', async ({ request }) => {
        created = (await request.json()) as Record<string, unknown>;
        return ok({ conversation: { token: 'room-new', display_name: 'Cousins', type: 2 } }, 'Created Cousins.');
      }),
    );
    renderWithProviders(<Family />);
    await user.click(await screen.findByTestId('chat-new'));
    await user.click(await screen.findByRole('button', { name: /Jeremiah/ }));
    await user.click(await screen.findByRole('button', { name: /Michele/ }));
    await user.type(screen.getByLabelText('Group name'), 'Cousins');
    await user.click(screen.getByTestId('new-chat-start'));
    await waitFor(() => expect(created).toEqual({ name: 'Cousins', participants: ['jeremiah', 'michele'] }));
  });

  it('lists members, removes one, saves the default, and deletes with a confirmation', async () => {
    const user = userEvent.setup();
    const removed: string[] = [];
    let deleted = '';
    let profilePatch: Record<string, unknown> | null = null;
    server.use(
      http.get('/api/communication/talk/conversations', () =>
        ok({ conversations: [{ token: 'room-alpha', display_name: 'Family', type: 2, can_moderate: true, last_message: 'Hi' }] }),
      ),
      http.get('/api/communication/talk/participants', () =>
        ok({
          participants: [
            { attendee_id: 1, actor_type: 'users', actor_id: 'default', display_name: 'Me', is_moderator: true },
            { attendee_id: 2, actor_type: 'users', actor_id: 'michele', display_name: 'Michele', is_moderator: false },
          ],
        }),
      ),
      http.delete('/api/communication/talk/participants', ({ request }) => {
        removed.push(new URL(request.url).searchParams.get('attendee_id') ?? '');
        return ok();
      }),
      http.delete('/api/communication/talk/conversations', ({ request }) => {
        deleted = new URL(request.url).searchParams.get('token') ?? '';
        return ok({ deleted: true });
      }),
      http.patch('/api/users/me', async ({ request }) => {
        profilePatch = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 1, username: 'default', chat_default_token: 'room-alpha' });
      }),
    );
    renderWithProviders(<Family />);
    await user.click(await screen.findByRole('button', { name: 'Conversation details' }));
    const info = await screen.findByTestId('conversation-info');
    expect(await within(info).findByText('Michele')).toBeInTheDocument();
    expect(within(info).queryByRole('button', { name: 'Remove Me' })).not.toBeInTheDocument();
    await user.click(within(info).getByRole('button', { name: 'Remove Michele' }));
    await waitFor(() => expect(removed).toEqual(['2']));

    await user.click(within(info).getByTestId('chat-default-toggle'));
    await waitFor(() => expect(profilePatch).toMatchObject({ chat_default_token: 'room-alpha' }));

    await user.click(within(info).getByTestId('chat-delete'));
    expect(deleted).toBe('');
    await user.click(within(info).getByTestId('chat-delete-confirm'));
    await waitFor(() => expect(deleted).toBe('room-alpha'));
  });

  it('offers to leave rather than delete when you do not run the conversation', async () => {
    const user = userEvent.setup();
    server.use(
      http.get('/api/communication/talk/conversations', () =>
        ok({ conversations: [{ token: 'room-alpha', display_name: 'Family', type: 2, can_moderate: false }] }),
      ),
      http.get('/api/communication/talk/participants', () => ok({ participants: [] })),
    );
    renderWithProviders(<Family />);
    await user.click(await screen.findByRole('button', { name: 'Conversation details' }));
    const info = await screen.findByTestId('conversation-info');
    expect(within(info).getByTestId('chat-delete')).toHaveTextContent('Leave conversation');
    expect(within(info).queryByTestId('chat-add-member')).not.toBeInTheDocument();
  });
});

describe('the redesigned chat', () => {
  it("pins today's verse, shares it from the Verse chip, and hides it until tomorrow", async () => {
    const user = userEvent.setup();
    const sent: string[] = [];
    localStorage.removeItem('family-chat-verse-dismissed');
    server.use(
      http.get('/api/bible/verse-of-day', () => HttpResponse.json({ reference: 'Joshua 24:15', text: 'As for me and my house, we will serve the LORD.' })),
      http.post('/api/communication/talk/messages', async ({ request }) => {
        sent.push(String(((await request.json()) as Record<string, unknown>).message));
        return ok({ message: { id: 999 } });
      }),
    );
    renderWithProviders(<Family />);
    const banner = await screen.findByTestId('chat-verse-banner');
    expect(banner).toHaveTextContent('Joshua 24:15');

    await user.click(within(screen.getByTestId('quick-actions')).getByRole('button', { name: /verse/i }));
    await waitFor(() => expect(sent[0]).toBe('📖 Joshua 24:15: "As for me and my house, we will serve the LORD."'));

    await user.click(within(banner).getByRole('button', { name: "Hide today's verse" }));
    expect(screen.queryByTestId('chat-verse-banner')).not.toBeInTheDocument();
    expect(localStorage.getItem('family-chat-verse-dismissed')).toBe(new Date().toISOString().slice(0, 10));
  });

  it('opens the Games tab from the chat', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Family />);
    await user.click(within(await screen.findByTestId('quick-actions')).getByRole('button', { name: /games/i }));
    expect(screen.getByRole('tab', { name: /games/i })).toHaveAttribute('aria-selected', 'true');
  });
});
