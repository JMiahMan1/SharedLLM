import { screen, waitFor } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import Settings from '../pages/Settings';
import { renderWithProviders } from './render';

describe('Settings System Configuration migration', () => {
  it('renders the migrated system settings section', async () => {
    renderWithProviders(<Settings />);

    expect(await screen.findByText('System Configuration')).toBeInTheDocument();
  });

  it('displays global settings fetched from the backend', async () => {
    renderWithProviders(<Settings />);

    await waitFor(() => {
      expect(screen.getByText('system_name')).toBeInTheDocument();
      expect(screen.getByText('system_log_level')).toBeInTheDocument();
    });
  });

  it('exposes an edit entry point for admins', async () => {
    renderWithProviders(<Settings />);

    const editButton = await screen.findByRole('button', { name: /edit/i });
    expect(editButton).toBeInTheDocument();
  });
});
