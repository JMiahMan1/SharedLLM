import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import Family from '../pages/Family';
import { callFallback } from '../context/CallContext';
import { renderWithProviders } from './render';
import { server } from './setup';

const ok = (detail: Record<string, unknown> = {}, message = 'ok') =>
  HttpResponse.json({ status: 'SUCCESS', message, service: 'talk', detail });

const conversations = [
  { token: 'room-family', display_name: 'Family', type: 2, can_start_call: true, last_activity: 20 },
  { token: 'room-michele', display_name: 'Michele', type: 1, can_start_call: true, last_activity: 10 },
  { token: 'room-note', display_name: 'Note to self', type: 6, can_start_call: false },
];

afterEach(() => {
  callFallback.activeCalls = [];
  vi.restoreAllMocks();
});

describe('family calls', () => {
  it('lists family rooms and starts an in-app call', async () => {
    const user = userEvent.setup();
    const startCall = vi.spyOn(callFallback, 'startCall').mockResolvedValue(undefined);
    server.use(
      http.get('/api/communication/talk/conversations', () => ok({ conversations })),
    );

    renderWithProviders(<Family />);
    await user.click(await screen.findByRole('tab', { name: 'Calls' }));

    const calls = await screen.findByTestId('family-calls');
    expect(await within(calls).findByText('Family')).toBeInTheDocument();
    expect(within(calls).getByText('Michele')).toBeInTheDocument();
    expect(within(calls).queryByText('Note to self')).not.toBeInTheDocument();
    expect(within(calls).queryByText(/microphone or speaker/i)).not.toBeInTheDocument();

    await user.click(within(calls).getByRole('button', { name: 'Drop in on Family' }));
    await waitFor(() => expect(startCall).toHaveBeenCalledWith('room-family', 'Family'));
  });

  it('offers to join a call that is already running', async () => {
    const user = userEvent.setup();
    callFallback.activeCalls = [
      {
        token: 'room-family',
        name: 'Family',
        started_at: 1_700_000_000,
        participants: [
          { id: 'peer-1', username: 'mom', name: 'Mom', muted: false, camera: false },
        ],
      },
    ];
    server.use(
      http.get('/api/communication/talk/conversations', () =>
        ok({
          conversations: [
            { token: 'room-family', display_name: 'Family', type: 2, can_start_call: true },
          ],
        }),
      ),
    );

    renderWithProviders(<Family />);
    await user.click(await screen.findByRole('tab', { name: 'Calls' }));

    expect(await screen.findByText('Mom in the call')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Join the Family call' })).toHaveTextContent('Join');
  });
});
