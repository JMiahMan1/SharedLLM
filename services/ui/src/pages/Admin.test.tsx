import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import Admin from './Admin';
import { renderWithProviders } from '../test/render';

vi.stubGlobal('confirm', vi.fn(() => true));

describe('Admin page', () => {
  it('renders live user, discovery, device, and settings sections', async () => {
    renderWithProviders(<Admin />);

    expect(await screen.findByText('User Management')).toBeInTheDocument();
    expect(screen.getByText('Discovery Import')).toBeInTheDocument();
    expect(screen.getByText('Device Assignments')).toBeInTheDocument();
    expect(screen.getByText('Global Settings')).toBeInTheDocument();
    expect(await screen.findByText('Shared/Default User')).toBeInTheDocument();
    expect(await screen.findByText('Jeremiah')).toBeInTheDocument();
  });

  it('creates a user from the modal and shows it in the list', async () => {
    renderWithProviders(<Admin />);

    fireEvent.click(await screen.findByText('Create User'));
    fireEvent.change(screen.getByLabelText(/^Username$/i), { target: { value: 'jeremiah' } });
    fireEvent.change(screen.getByLabelText(/^Display Name$/i), { target: { value: 'Jeremiah Summers' } });
    fireEvent.click(screen.getByText('Save User'));

    expect(await screen.findByText('Jeremiah Summers')).toBeInTheDocument();
  });

  it('imports a discovered user into the user list', async () => {
    renderWithProviders(<Admin />);

    const importButtons = await screen.findAllByText('Import');
    fireEvent.click(importButtons[0]);

    await waitFor(() => expect(screen.getAllByText('Jeremiah').length).toBeGreaterThan(0));
  });

  it('saves a new device assignment and a new setting', async () => {
    renderWithProviders(<Admin />);

    fireEvent.change(await screen.findByPlaceholderText('Home Assistant entity ID'), {
      target: { value: 'media_player.kitchen_echo' },
    });
    fireEvent.change(screen.getByLabelText('Device User'), {
      target: { value: 'default' },
    });
    fireEvent.click(screen.getByText('Save Assignment'));

    expect(await screen.findByText('media_player.kitchen_echo')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('new_setting_key'), { target: { value: 'feature_flag' } });
    fireEvent.change(screen.getByPlaceholderText('value'), { target: { value: 'enabled' } });
    fireEvent.click(screen.getByText('Add'));

    expect(await screen.findByDisplayValue('enabled')).toBeInTheDocument();
  });
});
