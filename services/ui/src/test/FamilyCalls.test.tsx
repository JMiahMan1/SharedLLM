import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import Family from '../pages/Family';
import { renderWithProviders } from './render';
import { server } from './setup';

const ok = (detail: Record<string, unknown> = {}, message = 'ok') =>
  HttpResponse.json({ status: 'SUCCESS', message, service: 'talk', detail });

const conversations = [
  { token: 'room-family', display_name: 'Family', type: 2, has_call: false, can_start_call: true, last_activity: 20 },
  { token: 'room-michele', display_name: 'Michele', type: 1, has_call: false, can_start_call: true, last_activity: 10 },
  { token: 'room-note', display_name: 'Note to self', type: 6, has_call: false, can_start_call: false },
];

function fakeTab() {
  const replace = vi.fn();
  const close = vi.fn();
  const tab = { opener: null, location: { replace }, close } as unknown as Window;
  return { tab, replace, close };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('family calls', () => {
  it('lists family rooms and opens the Talk call page', async () => {
    const user = userEvent.setup();
    const { tab, replace } = fakeTab();
    vi.spyOn(window, 'open').mockReturnValue(tab);
    server.use(
      http.get('/api/communication/talk/conversations', () => ok({ conversations })),
      http.post('/api/communication/talk/call/link', () => ok({ url: 'https://cloud.test/call/room-family' })),
    );

    renderWithProviders(<Family />);
    await user.click(await screen.findByRole('tab', { name: 'Calls' }));

    const calls = await screen.findByTestId('family-calls');
    expect(await within(calls).findByText('Family')).toBeInTheDocument();
    expect(within(calls).getByText('Michele')).toBeInTheDocument();
    expect(within(calls).queryByText('Note to self')).not.toBeInTheDocument();

    await user.click(within(calls).getByRole('button', { name: 'Start a call with Family' }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith('https://cloud.test/call/room-family'));
  });

  it('offers to join a call that is already running', async () => {
    const user = userEvent.setup();
    server.use(
      http.get('/api/communication/talk/conversations', () =>
        ok({
          conversations: [
            { token: 'room-family', display_name: 'Family', type: 2, has_call: true, can_start_call: true },
          ],
        }),
      ),
    );

    renderWithProviders(<Family />);
    await user.click(await screen.findByRole('tab', { name: 'Calls' }));

    expect(await screen.findByText('Call in progress — join it')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Join the Family call' })).toHaveTextContent('Join');
  });
});
