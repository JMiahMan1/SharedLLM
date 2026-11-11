import { describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import FamilyDropInSettings from '../components/settings/FamilyDropInSettings';

const people = [
  { username: 'mom', name: 'Mom', minor: false },
  { username: 'dad', name: 'Dad', minor: false },
  { username: 'kid', name: 'Kid', minor: true },
];

function serve(settings: unknown[], me = 'mom') {
  const saved: Array<{ url: string; body: unknown }> = [];
  server.use(
    http.get('/api/communication/calls/drop-in', () => HttpResponse.json({ me, people, settings })),
    http.put('/api/communication/calls/drop-in/*', async ({ request }) => {
      saved.push({ url: new URL(request.url).pathname, body: await request.json() });
      return HttpResponse.json({});
    }),
  );
  return saved;
}

describe('Family drop-in settings', () => {
  it('lets a parent choose who drops in on them and on a child, and switch a child\'s phone', async () => {
    const user = userEvent.setup();
    const saved = serve([
      { username: 'mom', name: 'Mom', minor: false, allow_from: ['dad'], configured: true, can_edit: true, devices: [] },
      {
        username: 'kid', name: 'Kid', minor: true, allow_from: [], configured: false, can_edit: true,
        devices: [{ device_key: 'kid-phone', label: "Kid's phone", kind: 'phone', drop_in: true }],
      },
    ]);
    render(<FamilyDropInSettings />);

    const mine = await screen.findByTestId('drop-in-mom');
    expect(within(mine).getByRole('button', { name: 'Dad' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(within(mine).getByRole('button', { name: 'Kid' }));

    const kid = screen.getByTestId('drop-in-kid');
    expect(within(kid).getByText('Nobody: every call rings.')).toBeInTheDocument();
    await user.click(within(kid).getByRole('button', { name: 'Mom' }));
    await user.click(within(kid).getByTestId('drop-in-device-kid-phone'));

    await waitFor(() => expect(saved).toEqual([
      { url: '/api/communication/calls/drop-in/mom', body: { allow_from: ['dad', 'kid'] } },
      { url: '/api/communication/calls/drop-in/kid', body: { allow_from: ['mom'] } },
      { url: '/api/communication/calls/drop-in/kid/devices/kid-phone', body: { drop_in: false } },
    ]));
  });

  it('shows a child their list read-only', async () => {
    serve([{ username: 'kid', name: 'Kid', minor: true, allow_from: ['mom'], configured: true, can_edit: false, devices: [] }], 'kid');
    render(<FamilyDropInSettings />);
    const mine = await screen.findByTestId('drop-in-kid');
    expect(within(mine).getByText('A parent chooses who can drop in on you.')).toBeInTheDocument();
    expect(within(mine).getByRole('button', { name: 'Mom' })).toBeDisabled();
  });
});
