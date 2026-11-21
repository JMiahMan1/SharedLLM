/**
 * Test accounts in the admin user list: GET /api/users leaves them out unless
 * asked with include_test_accounts=true, so the admin list asks, and marks
 * each one with a "Test account" badge. Other callers keep the default.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import Admin from '../pages/Admin';
import { api } from '../services/api';

const ACCOUNTS = [
  { id: 1, username: 'default', display_name: 'Shared/Default User', is_admin: true, is_system_default: true, is_test_account: false },
  { id: 2, username: 'e2e-tester', display_name: 'E2E Tester', is_admin: false, is_test_account: true },
];

describe('test account badge', () => {
  afterEach(() => server.resetHandlers());

  it('the admin user list asks for test accounts and badges them', async () => {
    const asked: string[] = [];
    server.use(
      http.get('/api/users', ({ request }) => {
        const include = new URL(request.url).searchParams.get('include_test_accounts');
        asked.push(include ?? '');
        return HttpResponse.json(include === 'true' ? ACCOUNTS : ACCOUNTS.filter((u) => !u.is_test_account));
      }),
      http.get('/api/geo/stars', () => HttpResponse.json({ user_id: 'default', stars: 0, grants: [] })),
    );
    renderWithProviders(<Admin />, { route: '/admin/users' });

    const button = await screen.findByRole('button', { name: 'Reset sign-in for e2e-tester' });
    const tester = button.closest('.glass-card') as HTMLElement;
    expect(within(tester).getByTestId('test-account-badge')).toHaveTextContent('Test account');
    const regular = screen.getByRole('button', { name: 'Reset sign-in for default' }).closest('.glass-card') as HTMLElement;
    expect(within(regular).queryByTestId('test-account-badge')).not.toBeInTheDocument();
    expect(asked).toContain('true');
  });

  it('getUsers keeps the default for every other caller', async () => {
    const asked: Array<string | null> = [];
    server.use(http.get('/api/users', ({ request }) => {
      asked.push(new URL(request.url).searchParams.get('include_test_accounts'));
      return HttpResponse.json([]);
    }));
    await api.getUsers();
    await api.getUsers({ includeTestAccounts: true });
    expect(asked).toEqual([null, 'true']);
  });
});
