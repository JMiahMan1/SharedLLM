import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import AbuseChKeyTile from './AbuseChKeyTile';
import { api } from '../../services/api';
import type { UserProfile } from '../../services/api';
import toast from 'react-hot-toast';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), updateProfile: vi.fn(), revealCredential: vi.fn() },
  };
});

const user = (extra: Partial<UserProfile>): UserProfile =>
  ({ username: 'sam', full_name: 'Sam', role: 'user', is_admin: false, saved_credentials: [], ...extra }) as UserProfile;

const wrap = (u: UserProfile) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <AbuseChKeyTile userData={u} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('AbuseChKeyTile', () => {
  beforeEach(() => vi.clearAllMocks());

  it('says the household key is used, and saves an own key', async () => {
    vi.mocked(api.updateProfile).mockResolvedValue(user({}));
    wrap(user({ abuse_ch_status: 'household' }));
    expect(screen.getByTestId('integration-status-abuse.ch')).toHaveTextContent('Using the household key');
    fireEvent.click(screen.getByRole('button', { name: 'Manage abuse.ch' }));
    const dialog = await screen.findByTestId('abuse-ch-dialog');
    expect(within(dialog).getByTestId('abuse-ch-status')).toHaveTextContent('An admin set a key for the whole household');
    expect(dialog).toHaveTextContent('auth.abuse.ch');
    expect(within(dialog).queryByRole('button', { name: /Remove/ })).toBeNull();
    fireEvent.change(within(dialog).getByLabelText('abuse.ch Auth-Key'), { target: { value: ' made-up-key ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.updateProfile).toHaveBeenCalledWith({ malwarebazaar_key: 'made-up-key' }));
  });

  it('never fetches a saved key for the eye, and removes it by name', async () => {
    vi.mocked(api.updateProfile).mockResolvedValue(user({}));
    wrap(user({ abuse_ch_status: 'own', saved_credentials: ['malwarebazaar_key'] }));
    expect(screen.getByTestId('integration-status-abuse.ch')).toHaveTextContent('Using your own key');
    fireEvent.click(screen.getByRole('button', { name: 'Manage abuse.ch' }));
    const dialog = await screen.findByTestId('abuse-ch-dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Show abuse.ch Auth-Key' }));
    expect(api.revealCredential).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove my key' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(api.updateProfile).toHaveBeenCalledWith({ clear_fields: ['malwarebazaar_key'] }));
  });

  it('shows abuse.ch refusing the key in the server\'s words', async () => {
    const toastError = vi.spyOn(toast, 'error');
    vi.mocked(api.updateProfile).mockRejectedValue({ response: { data: { detail: 'abuse.ch refused this Auth-Key, so it was not saved.' } } });
    wrap(user({ abuse_ch_status: 'not_set_up' }));
    expect(screen.getByTestId('integration-status-abuse.ch')).toHaveTextContent('Not set up');
    fireEvent.click(screen.getByRole('button', { name: 'Manage abuse.ch' }));
    const dialog = await screen.findByTestId('abuse-ch-dialog');
    fireEvent.change(within(dialog).getByLabelText('abuse.ch Auth-Key'), { target: { value: 'made-up-bad' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith('abuse.ch refused this Auth-Key, so it was not saved.'));
  });

  it('on the default user, it is the household key', () => {
    wrap(user({ is_system_default: true, abuse_ch_status: 'own', saved_credentials: ['malwarebazaar_key'] }));
    expect(screen.getByTestId('integration-status-abuse.ch')).toHaveTextContent('Household key set');
  });
});
