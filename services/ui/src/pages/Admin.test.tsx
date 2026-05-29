import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import Admin from './Admin';
import { renderWithProviders } from '../test/render';
import { api } from '../services/api';

vi.mock('../components/ui/EntitySearchDropdown', () => ({
  default: ({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) => (
    <input
      data-testid="entity-search"
      type="text"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));

vi.stubGlobal('confirm', vi.fn(() => true));

describe('Admin page', () => {
  it('api.discoverUsers returns discovered users via MSW', async () => {
    const result = await api.discoverUsers();
    expect(result.users).toHaveLength(2);
    expect(result.users[0].username).toBe('jeremiah');
  });

  it('renders discovery users when no filter is applied', async () => {
    renderWithProviders(<Admin />);
    
    await screen.findByText('User Management');
    await screen.findByText('Discovery Import');
    await screen.findByText('Shared/Default User');
    
    await waitFor(() => {
      expect(screen.getByText('Jeremiah')).toBeInTheDocument();
    }, { timeout: 5000 });
    
    expect(screen.getByText('Jeremiah')).toBeInTheDocument();
    expect(screen.getByText('Michele')).toBeInTheDocument();
  });
  it('renders tab navigation and default users tab', async () => {
    renderWithProviders(<Admin />);

    expect(await screen.findByText('Users & Devices')).toBeInTheDocument();
    expect(screen.getByText('Raven Ops')).toBeInTheDocument();
    expect(screen.getByText('LLM & Settings')).toBeInTheDocument();
    expect(screen.getByText('Database & Audit')).toBeInTheDocument();
    expect(await screen.findByText('User Management')).toBeInTheDocument();
    expect(await screen.findByText('Shared/Default User')).toBeInTheDocument();
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

    await screen.findByText('Discovery Import');
    
    const jeremiahElement = await screen.findByText('Jeremiah');
    expect(jeremiahElement).toBeInTheDocument();
    
    const importButtons = await screen.findAllByText('Import');
    expect(importButtons.length).toBeGreaterThan(0);
    fireEvent.click(importButtons[0]);

    await waitFor(() => expect(screen.getAllByText('Jeremiah').length).toBeGreaterThan(0));
  });

  it('saves a new device assignment and a new setting', async () => {
    renderWithProviders(<Admin />);

    fireEvent.change(await screen.findByTestId('entity-search'), {
      target: { value: 'media_player.kitchen_echo' },
    });
    fireEvent.change(screen.getByLabelText('Device User'), {
      target: { value: 'default' },
    });
    fireEvent.click(screen.getByText('Save Assignment'));

    expect(await screen.findByText('media_player.kitchen_echo')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Database & Audit'));
    fireEvent.change(await screen.findByPlaceholderText('new_setting_key'), { target: { value: 'feature_flag' } });
    fireEvent.change(screen.getByPlaceholderText('value'), { target: { value: 'enabled' } });
    fireEvent.click(screen.getByText('Add'));

    expect(await screen.findByDisplayValue('enabled')).toBeInTheDocument();
  });

  it('shows Raven Ops panel when tab is clicked', async () => {
    renderWithProviders(<Admin />);

    fireEvent.click(await screen.findByText('Raven Ops'));
    expect(await screen.findByText('Autonomous Ops (Raven)')).toBeInTheDocument();
    expect(screen.getByText('Pending Triage Queue')).toBeInTheDocument();
    expect(screen.getByText('Active Missions Monitor')).toBeInTheDocument();
  });

  it('shows LLM settings when tab is clicked', async () => {
    renderWithProviders(<Admin />);

    fireEvent.click(screen.getByText('LLM & Settings'));
    expect(await screen.findByText('Local Model Mapping')).toBeInTheDocument();
  });

  it('shows database insights when tab is clicked', async () => {
    renderWithProviders(<Admin />);

    fireEvent.click(screen.getByText('Database & Audit'));
    expect(await screen.findByText('Advanced Database Insights')).toBeInTheDocument();
    expect(screen.getByText('Audit Trail')).toBeInTheDocument();
  });
});
