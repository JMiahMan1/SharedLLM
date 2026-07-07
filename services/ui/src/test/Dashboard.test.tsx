import { screen, waitFor } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import Dashboard from '../pages/Dashboard';
import { renderWithProviders } from './render';

describe('Dashboard decluttering', () => {
  it('no longer renders the cluttered System Settings list on the main dashboard', async () => {
    renderWithProviders(<Dashboard />);

    // Dashboard should still render its header.
    await waitFor(() => {
      expect(screen.getByText('Jarvis Dashboard')).toBeInTheDocument();
    });

    // The long-list settings panel must be migrated away from the dashboard.
    expect(screen.queryByText('System Settings')).not.toBeInTheDocument();
  });
});
