import { fireEvent, screen, within } from '@testing-library/react';
import { beforeEach, describe, it, expect } from 'vitest';
import Sidebar from '../components/layout/Sidebar';
import { renderWithProviders } from './render';

describe('Sidebar Component', () => {
  it('renders the brand title', () => {
    renderWithProviders(<Sidebar />);

    expect(screen.getByText('Jarvis OS')).toBeInTheDocument();
  });

  it('renders navigation links, including the admin-only ones for an admin', async () => {
    renderWithProviders(<Sidebar />);

    expect(screen.getByText('Dashboard')).toBeInTheDocument();
    expect(screen.getByText('Workspaces')).toBeInTheDocument();
    expect(screen.getByText('Media')).toBeInTheDocument();
    expect(screen.getByText('Docs')).toBeInTheDocument();
    // The test account is an admin; Lab and Admin appear once the profile loads.
    expect(await screen.findByText('Lab')).toBeInTheDocument();
    expect(screen.getByText('Admin')).toBeInTheDocument();
  });

  it('reports the real service status and build, not a fixed "nominal"', async () => {
    renderWithProviders(<Sidebar />);

    expect(await screen.findByText('All services online')).toBeInTheDocument();
    expect(await screen.findByText('abc1234')).toBeInTheDocument();
    expect(screen.queryByText(/v1\.0\.0-alpha/)).toBeNull();
  });

  describe('sections', () => {
    beforeEach(() => localStorage.removeItem('jarvis_sidebar_collapsed_v1'));

    it('groups the pages under headings, with the home controls under Home', async () => {
      renderWithProviders(<Sidebar />);
      const home = screen.getByTestId('nav-section-home');
      expect(within(home).getByRole('button', { name: /Home/ })).toHaveAttribute('aria-expanded', 'true');
      for (const label of ['Rooms', 'Climate', 'Cameras', 'Energy']) expect(within(home).getByText(label)).toBeInTheDocument();
      const manage = screen.getByTestId('nav-section-manage');
      for (const label of ['Schedules', 'Automations', 'History']) expect(within(manage).getByText(label)).toBeInTheDocument();
      // Admin-only pages appear under System once the admin profile loads.
      const system = screen.getByTestId('nav-section-system');
      expect(await within(system).findByText('Admin')).toBeInTheDocument();
    });

    it('folds a section and remembers it, but never hides the section of the open page', () => {
      renderWithProviders(<Sidebar />, { route: '/' });
      const toggle = within(screen.getByTestId('nav-section-manage')).getByRole('button', { name: /Manage home/ });
      fireEvent.click(toggle);
      expect(toggle).toHaveAttribute('aria-expanded', 'false');
      expect(localStorage.getItem('jarvis_sidebar_collapsed_v1')).toContain('manage');

      localStorage.setItem('jarvis_sidebar_collapsed_v1', JSON.stringify(['home']));
      renderWithProviders(<Sidebar />, { route: '/climate' });
      const homeButtons = screen.getAllByRole('button', { name: /^Home/ });
      expect(homeButtons[homeButtons.length - 1]).toHaveAttribute('aria-expanded', 'true');
    });
  });
});
