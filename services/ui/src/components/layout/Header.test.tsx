import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Header from './Header';

const logout = vi.fn();

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: mockUser, logout, isAuthenticated: true }),
  __esModule: true,
}));

vi.mock('../../context/LocationContext', () => ({
  useLocation: () => ({ isTracking: false }),
  __esModule: true,
}));

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getHealth: vi.fn().mockResolvedValue({ status: 'ok' }),
      getLogs: vi.fn().mockResolvedValue([]),
      getUpdateStatus: vi.fn().mockResolvedValue({ updates_available: false }),
      pollUpdateStatus: vi.fn().mockResolvedValue({ updates_available: false }),
    },
  };
});

let mockUser: { username: string; is_admin: boolean } | null = null;

function renderHeader() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<Header />} />
          <Route path="/settings" element={<div>Settings Page</div>} />
          <Route path="/identity" element={<div>Identity Page</div>} />
          <Route path="/admin" element={<div>Admin Page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Header profile menu', () => {
  beforeEach(() => {
    logout.mockClear();
    mockUser = { username: 'jeremiah', is_admin: true };
  });

  it('does not log out when the avatar is pressed — it opens a menu', () => {
    renderHeader();
    fireEvent.click(screen.getByLabelText('Account menu'));
    expect(screen.getByTestId('profile-menu')).toBeTruthy();
    expect(logout).not.toHaveBeenCalled();
  });

  it('offers profile settings and navigates there', async () => {
    renderHeader();
    fireEvent.click(screen.getByLabelText('Account menu'));
    fireEvent.click(screen.getByRole('menuitem', { name: /profile settings/i }));
    await waitFor(() => expect(screen.getByText('Settings Page')).toBeTruthy());
  });

  it('shows the admin panel entry only to admins', () => {
    renderHeader();
    fireEvent.click(screen.getByLabelText('Account menu'));
    expect(screen.getByRole('menuitem', { name: /admin control panel/i })).toBeTruthy();
  });

  it('hides the admin entry from family members', () => {
    mockUser = { username: 'kiddo', is_admin: false };
    renderHeader();
    fireEvent.click(screen.getByLabelText('Account menu'));
    expect(screen.queryByRole('menuitem', { name: /admin control panel/i })).toBeNull();
  });

  it('logs out from the last menu item', () => {
    renderHeader();
    fireEvent.click(screen.getByLabelText('Account menu'));
    fireEvent.click(screen.getByTestId('profile-menu-logout'));
    expect(logout).toHaveBeenCalledTimes(1);
  });

  it('closes on Escape and on outside click', () => {
    renderHeader();
    fireEvent.click(screen.getByLabelText('Account menu'));
    expect(screen.getByTestId('profile-menu')).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByTestId('profile-menu')).toBeNull();

    fireEvent.click(screen.getByLabelText('Account menu'));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByTestId('profile-menu')).toBeNull();
  });
});
