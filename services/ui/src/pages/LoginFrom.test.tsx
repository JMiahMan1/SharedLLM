import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

/**
 * Signing in on the way somewhere.
 *
 * A tapped notification can point at a page that needs a session. The page is
 * what they wanted to read, so the sign-in form must hand them back to it
 * instead of dropping them on the dashboard to find it again.
 */

const m = vi.hoisted(() => ({
  login: vi.fn(async () => undefined),
  storageGet: vi.fn(async () => null),
  storageSet: vi.fn(async () => undefined),
}));

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ login: m.login, initError: null }),
}));
vi.mock('../lib/storage', () => ({
  storageGet: (...a: Parameters<typeof m.storageGet>) => m.storageGet(...a),
  storageSet: (...a: Parameters<typeof m.storageSet>) => m.storageSet(...a),
}));
vi.mock('../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));
vi.mock('@capacitor/core', () => ({ Capacitor: { isNativePlatform: () => false } }));

import Login from './Login';

function Where() {
  const location = useLocation();
  return <p data-testid="where">{`${location.pathname}${location.search}`}</p>;
}

function mount(state?: unknown) {
  render(
    <MemoryRouter initialEntries={[{ pathname: '/login', state }]}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function signIn() {
  fireEvent.change(screen.getByLabelText(/username/i, { selector: 'input' }), { target: { value: 'alex' } });
  fireEvent.change(screen.getByLabelText(/password/i, { selector: 'input' }), { target: { value: 'secret' } });
  fireEvent.click(screen.getByRole('button', { name: /sign in/i }));
  await waitFor(() => expect(m.login).toHaveBeenCalled());
}

describe('signing in from a page that needed it', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    m.login.mockResolvedValue(undefined);
  });

  it('goes back to the page the tap asked for', async () => {
    mount({ from: '/family?chat=abc&beloved=notes' });
    await signIn();
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/family?chat=abc&beloved=notes'));
  });

  it('goes home when nothing asked for a page', async () => {
    mount();
    await signIn();
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/'));
  });

  it('never returns to the login form itself', async () => {
    // A stale "from" pointing at /login would be a loop.
    mount({ from: '/login?next=/login' });
    await signIn();
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/'));
  });

  it('refuses a page that is not Jarvis', async () => {
    mount({ from: 'https://example.com/phish' });
    await signIn();
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/'));
  });
});
