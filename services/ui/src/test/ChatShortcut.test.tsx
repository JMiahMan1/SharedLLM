import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import Family from '../pages/Family';
import { renderWithProviders } from './render';
import { server } from './setup';
import type { ChatShortcutPluginInterface, ChatShortcutRequest } from '../plugins/chatShortcut';
import toast from 'react-hot-toast';

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));

const ok = (detail: Record<string, unknown> = {}) => HttpResponse.json({ status: 'SUCCESS', message: 'ok', service: 'talk', detail });

const DIRECTORY = [
  { username: 'default', display_name: 'Me', nextcloud_user: 'default', avatar: null },
  { username: 'sam', display_name: 'Sam Example', nextcloud_user: 'sam', avatar: null },
];

function conversations(list: Array<Record<string, unknown>>) {
  server.use(
    http.get('/api/communication/talk/conversations', () => ok({ conversations: list })),
    http.get('/api/communication/talk/participants', () => ok({ participants: [] })),
    http.get('/api/users/family-directory', () => HttpResponse.json(DIRECTORY)),
  );
}

function stubPlugin(supported = true) {
  const pinned: ChatShortcutRequest[] = [];
  const disabled: Array<{ id: string; message: string }> = [];
  const plugin: ChatShortcutPluginInterface = {
    supported: vi.fn(async () => ({ supported })),
    pin: vi.fn(async (request: ChatShortcutRequest) => {
      pinned.push(request);
      return { requested: true, updated: false };
    }),
    disable: vi.fn(async (options: { id: string; message: string }) => {
      disabled.push(options);
    }),
  };
  window.__jarvisChatShortcutMock = plugin;
  return { pinned, disabled };
}

afterEach(() => {
  delete window.__jarvisChatShortcutMock;
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
});

async function openDetails(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Conversation details' }));
  return screen.findByTestId('conversation-info');
}

describe('chat home-screen shortcuts', () => {
  it('is never offered on the web', async () => {
    const user = userEvent.setup();
    conversations([{ token: 'room-sam', display_name: 'Sam Example', name: 'sam', type: 1 }]);
    renderWithProviders(<Family />);
    const info = await openDetails(user);
    expect(await within(info).findByTestId('chat-default-toggle')).toBeInTheDocument();
    expect(within(info).queryByTestId('chat-home-shortcut')).not.toBeInTheDocument();
  });

  it('is not offered when the launcher cannot pin shortcuts', async () => {
    const user = userEvent.setup();
    stubPlugin(false);
    conversations([{ token: 'room-sam', display_name: 'Sam Example', name: 'sam', type: 1 }]);
    renderWithProviders(<Family />);
    const info = await openDetails(user);
    await waitFor(() => expect(window.__jarvisChatShortcutMock?.supported).toHaveBeenCalled());
    expect(within(info).queryByTestId('chat-home-shortcut')).not.toBeInTheDocument();
  });

  it("adds a one-to-one chat with the other person's name, its page and their initials", async () => {
    const user = userEvent.setup();
    const { pinned } = stubPlugin();
    conversations([{ token: 'room-sam', display_name: 'Sam Example', name: 'sam', type: 1, last_message: 'A secret plan' }]);
    renderWithProviders(<Family />);
    const info = await openDetails(user);
    await user.click(await within(info).findByTestId('chat-home-shortcut'));
    await waitFor(() => expect(pinned).toHaveLength(1));
    expect(pinned[0]).toEqual({ id: 'chat:room-sam', label: 'Sam Example', path: '/family?chat=room-sam', color: expect.stringMatching(/^#[0-9A-Fa-f]{6}$/), monogram: 'SE' });
    expect(JSON.stringify(pinned[0])).not.toContain('A secret plan');
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Added to home screen'));
  });

  it('greys out the shortcut of a deleted conversation', async () => {
    const user = userEvent.setup();
    const { disabled } = stubPlugin();
    conversations([{ token: 'room-alpha', display_name: 'Family', type: 2, can_moderate: true }]);
    server.use(http.delete('/api/communication/talk/conversations', () => ok({ deleted: true })));
    renderWithProviders(<Family />);
    const info = await openDetails(user);
    await user.click(within(info).getByTestId('chat-delete'));
    await user.click(within(info).getByTestId('chat-delete-confirm'));
    await waitFor(() => expect(disabled).toEqual([{ id: 'chat:room-alpha', message: 'This chat is no longer available' }]));
  });

  it('a shortcut to a conversation that is gone lands on the list with a notice', async () => {
    const { disabled } = stubPlugin();
    conversations([{ token: 'room-alpha', display_name: 'Family', type: 2 }]);
    renderWithProviders(<Family />, { route: '/family?chat=room-gone' });
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('This chat is no longer available'));
    await waitFor(() => expect(disabled).toEqual([{ id: 'chat:room-gone', message: 'This chat is no longer available' }]));
    // The list, not an empty thread for a conversation that is not there.
    expect(screen.queryByText('Select a conversation')).not.toBeInTheDocument();
  });

  it('a shortcut to a conversation that exists opens it and greys nothing out', async () => {
    const { disabled } = stubPlugin();
    conversations([{ token: 'room-alpha', display_name: 'Family', type: 2 }, { token: 'room-sam', display_name: 'Sam Example', name: 'sam', type: 1 }]);
    renderWithProviders(<Family />, { route: '/family?chat=room-sam' });
    const thread = await screen.findByTestId('chat-thread');
    await waitFor(() => expect(within(thread).getByText('Sam Example')).toBeInTheDocument());
    await new Promise((r) => setTimeout(r, 300));
    expect(disabled).toEqual([]);
    expect(toast.error).not.toHaveBeenCalled();
  });
});
