/**
 * Admin > Security: "Sign everyone out and reset keys", the per-person
 * "Reset sign-in", and the "Test account" badge on the admin user lists.
 *
 * The confirm stays off until RESET is typed; the result lists the counts,
 * the home hub's answer, what has to be enrolled again by hand (grouped by
 * kind) and the third-party logins to rotate at the provider. The admin's own
 * key is reset too, so "Sign in again" signs out with the reset reason.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import type { SignInResetPreview, SignInResetResult } from '../services/api';

const logout = vi.fn().mockResolvedValue(undefined);
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'parent', is_admin: true }, logout }),
}));

const { default: SignInResetPanel, UserSignInResetButton } = await import('../components/admin/SignInResetPanel');

const RE_ENROL = [
  { kind: 'windows_guard', label: 'Study PC', owner: 'kid', how: 'Run the Windows Guard installer again and sign in.' },
  { kind: 'windows_guard', label: 'Den laptop', owner: null, how: 'Run the Windows Guard installer again and sign in.' },
  { kind: 'esphome_satellite', label: 'Kitchen satellite', owner: null, count: 2, how: 'Re-flash with the new key from Devices.' },
  { kind: 'api_key', label: 'Chat client', owner: 'parent', how: 'Make a new key in Accounts and paste it into the app.' },
] as SignInResetPreview['re_enrol'];

const THIRD_PARTY = [{ field: 'github_token', label: 'GitHub token', accounts: 2 }];

const PREVIEW: SignInResetPreview = {
  users: 4, api_keys: 9, device_agent_keys: 3, re_enrol: RE_ENROL, third_party: THIRD_PARTY, last_reset: null,
};

function result(overrides: Partial<SignInResetResult> = {}): SignInResetResult {
  return {
    status: 'SUCCESS', scope: 'everyone', username: null, at: '2026-11-21T10:00:00', by: 'parent',
    users_reset: 4, api_keys_revoked: 9, device_agent_keys_revoked: 3,
    homehub: { status: 'revoked', revoked: 2, message: 'Revoked 2 HomeHub tokens.' },
    re_enrol: RE_ENROL, third_party: THIRD_PARTY, caller_signed_out: true,
    ...overrides,
  };
}

function renderUi(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe('Admin security panel', () => {
  let posts: unknown[];
  beforeEach(() => {
    posts = [];
    logout.mockClear();
    server.use(
      http.get('/api/users/security/sign-in-reset', () => HttpResponse.json(PREVIEW)),
      http.post('/api/users/security/sign-in-reset', async ({ request }) => {
        posts.push(await request.json());
        return HttpResponse.json(result());
      }),
    );
  });
  afterEach(() => server.resetHandlers());

  it('previews, waits for RESET, and reports what needs enrolling again', async () => {
    const user = userEvent.setup();
    renderUi(<SignInResetPanel />);
    await user.click(screen.getByRole('button', { name: 'Sign everyone out and reset keys' }));

    const dialog = await screen.findByRole('dialog');
    const preview = await within(dialog).findByTestId('sign-in-reset-preview');
    expect(within(preview).getByText('9')).toBeInTheDocument();
    expect(within(preview).getByText('Windows Guard')).toBeInTheDocument();
    expect(within(preview).getByText('GitHub token')).toBeInTheDocument();

    const confirm = within(dialog).getByTestId('sign-in-reset-confirm-button');
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByLabelText('Type RESET to confirm'), 'reset');
    expect(confirm).toBeDisabled();
    await user.clear(within(dialog).getByLabelText('Type RESET to confirm'));
    await user.type(within(dialog).getByLabelText('Type RESET to confirm'), 'RESET');
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    const report = await within(dialog).findByTestId('sign-in-reset-report');
    expect(posts).toEqual([{ confirm: 'RESET' }]);
    expect(within(report).getByTestId('homehub-status')).toHaveTextContent('Revoked 2 HomeHub tokens.');
    const guards = within(report).getByTestId('reenrol-windows_guard');
    expect(within(guards).getByText('Study PC')).toBeInTheDocument();
    expect(within(guards).getByText('(@kid)')).toBeInTheDocument();
    expect(within(guards).getByText('Den laptop')).toBeInTheDocument();
    expect(within(report).getByTestId('reenrol-esphome_satellite')).toHaveTextContent('Re-flash with the new key from Devices.');
    expect(within(report).getByTestId('reenrol-api_key')).toHaveTextContent('Chat client');
    expect(within(report).getByText(/rotate these at the provider/i)).toBeInTheDocument();
    expect(within(report).getByTestId('third-party-list')).toHaveTextContent('GitHub token');

    await user.click(within(dialog).getByTestId('sign-in-again'));
    expect(logout).toHaveBeenCalledWith('sign_in_reset');
  });

  it('says so when the reset fails, and changes nothing', async () => {
    server.use(http.post('/api/users/security/sign-in-reset', () =>
      HttpResponse.json({ detail: 'Identity database is read-only' }, { status: 500 })));
    const user = userEvent.setup();
    renderUi(<SignInResetPanel />);
    await user.click(screen.getByRole('button', { name: 'Sign everyone out and reset keys' }));
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByTestId('sign-in-reset-preview');
    await user.type(within(dialog).getByLabelText('Type RESET to confirm'), 'RESET');
    await user.click(within(dialog).getByTestId('sign-in-reset-confirm-button'));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Identity database is read-only');
    expect(logout).not.toHaveBeenCalled();
  });

  it('shows the last reset report after signing back in', async () => {
    server.use(http.get('/api/users/security/sign-in-reset/last', () => HttpResponse.json(result())));
    renderUi(<SignInResetPanel />);
    const last = await screen.findByTestId('last-sign-in-reset');
    expect(within(last).getByTestId('homehub-status')).toHaveTextContent('Revoked 2 HomeHub tokens.');
    expect(within(last).getByTestId('reenrol-list')).toHaveTextContent('Kitchen satellite');
  });
});

describe('per-person sign-in reset', () => {
  afterEach(() => server.resetHandlers());

  it('asks first, then resets only that person', async () => {
    const posts: unknown[] = [];
    server.use(http.post('/api/users/security/sign-in-reset', async ({ request }) => {
      posts.push(await request.json());
      return HttpResponse.json(result({
        scope: 'user', username: 'kid', users_reset: 1, api_keys_revoked: 2, caller_signed_out: false,
        homehub: { status: 'skipped', revoked: 0, message: 'HomeHub is not connected.' },
        re_enrol: [RE_ENROL[0]],
      }));
    }));
    const user = userEvent.setup();
    renderUi(<UserSignInResetButton username="kid" name="Kid" />);
    await user.click(screen.getByRole('button', { name: 'Reset sign-in for kid' }));
    const confirm = await screen.findByRole('dialog');
    expect(confirm).toHaveTextContent("Reset Kid's sign-in?");
    await user.click(within(confirm).getByRole('button', { name: 'Reset sign-in' }));

    const report = await screen.findByTestId('sign-in-reset-report');
    expect(posts).toEqual([{ confirm: 'RESET', username: 'kid' }]);
    expect(report).toHaveTextContent('Sign-in reset for @kid');
    expect(within(report).getByTestId('homehub-status')).toHaveTextContent('HomeHub is not connected.');
    expect(screen.queryByTestId('sign-in-again')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
  });

  it('does nothing when the admin cancels', async () => {
    const posts: unknown[] = [];
    server.use(http.post('/api/users/security/sign-in-reset', async ({ request }) => {
      posts.push(await request.json());
      return HttpResponse.json(result());
    }));
    const user = userEvent.setup();
    renderUi(<UserSignInResetButton username="kid" />);
    await user.click(screen.getByRole('button', { name: 'Reset sign-in for kid' }));
    const confirm = await screen.findByRole('dialog');
    await user.click(within(confirm).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(posts).toEqual([]);
  });
});
