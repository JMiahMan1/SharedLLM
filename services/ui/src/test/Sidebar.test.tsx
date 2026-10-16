import { screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
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
});
