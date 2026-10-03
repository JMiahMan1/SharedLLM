import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import Admin from '../pages/Admin';

/**
 * Admin bonus stars.
 *
 * The grant is only *half* a feature: the ledger write lands in Jarvis and then
 * mirrors to the recipient's Skylight account, and those two can disagree. So
 * what is pinned here is that the admin surface asks who the stars are for
 * before offering a form at all — a stars panel that defaults to the first user
 * in the list is a panel that will eventually award stars to the wrong person.
 */
describe('admin bonus stars', () => {
  beforeEach(() => {
    // The star balance is a real query; leaving it unhandled leaves it pending
    // forever and the panel sits on its loading skeleton for the whole test.
    server.use(
      http.get('/api/geo/stars', () =>
        HttpResponse.json({ user_id: 'default', stars: 4, grants: [] })
      )
    );
  });

  afterEach(() => server.resetHandlers());

  async function openStars() {
    const user = userEvent.setup();
    renderWithProviders(<Admin />);
    const picker = await screen.findByLabelText('Star recipient');
    // The picker is empty until `/api/users` lands; selecting before then would
    // be selecting nothing, and the test would pass for the wrong reason.
    await screen.findByRole('option', { name: /@default/ });
    return { user, picker };
  }

  it('asks who the stars are for before showing any form', async () => {
    const { picker } = await openStars();

    expect(await screen.findByTestId('admin-stars')).toBeInTheDocument();
    expect(picker).toHaveValue('');
    expect(screen.queryByTestId('star-balance')).not.toBeInTheDocument();
    expect(screen.getByText(/nothing is granted until someone is chosen/i)).toBeInTheDocument();
  });

  it('offers the mirroring grant once a recipient is chosen', async () => {
    const { user, picker } = await openStars();

    await user.selectOptions(picker, 'default');

    expect(await screen.findByTestId('star-balance')).toBeInTheDocument();
    // Admin mode: the mirror is visible and controllable, not implied.
    expect(screen.getByRole('checkbox', { name: /mirror into default/i })).toBeChecked();
    expect(screen.getByRole('button', { name: /grant/i })).toBeEnabled();
  });

  it('re-locks the panel when the recipient is cleared', async () => {
    const { user, picker } = await openStars();
    await user.selectOptions(picker, 'default');
    await screen.findByTestId('star-balance');

    await user.selectOptions(picker, '');

    await waitFor(() => expect(screen.queryByTestId('star-balance')).not.toBeInTheDocument());
  });
});