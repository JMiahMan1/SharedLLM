import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { Capacitor } from '@capacitor/core';
import Settings from '../pages/Settings';
import { renderWithProviders } from './render';
import { setAdminPin, clearAdminPin, verifyAdminPin } from '../lib/adminPin';

afterEach(() => {
  vi.restoreAllMocks();
  clearAdminPin();
});

describe('Settings page', () => {
  it('links admins to system configuration instead of dumping raw settings', async () => {
    renderWithProviders(<Settings />);

    expect(await screen.findByRole('button', { name: /system configuration/i })).toBeInTheDocument();
    // The old section printed every GlobalSetting (tokens included) inline.
    await waitFor(() => expect(screen.queryByText('system_log_level')).not.toBeInTheDocument());
  });

  it('has no dead Personal Integrations button; Identity covers integrations', async () => {
    renderWithProviders(<Settings />);

    expect(await screen.findByRole('button', { name: /accounts & integrations/i })).toBeInTheDocument();
    expect(screen.queryByText('Personal Integrations')).not.toBeInTheDocument();
  });

  it('describes how notifications reach this device instead of a toggle that did nothing', async () => {
    renderWithProviders(<Settings />);

    const row = await screen.findByTestId('notifications-row');
    expect(row).toHaveTextContent(/notifications/i);
    // The test DOM has no PushManager: there is nothing to switch, so no switch.
    expect(screen.queryByRole('switch', { name: /notifications/i })).not.toBeInTheDocument();
  });

  it('offers a switch per kind of notice, and the push app list only in the app', async () => {
    renderWithProviders(<Settings />);

    expect(await screen.findByRole('switch', { name: 'Family chat' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /apps using jarvis for push/i })).not.toBeInTheDocument();
  });

  it('lists apps using Jarvis for push in the Android app', async () => {
    vi.spyOn(Capacitor, 'isNativePlatform').mockReturnValue(true);
    renderWithProviders(<Settings />);

    expect(await screen.findByRole('heading', { name: /apps using jarvis for push/i })).toBeInTheDocument();
  });

  it('hides the admin PIN on the web, where it gates nothing', async () => {
    renderWithProviders(<Settings />);
    await screen.findByRole('button', { name: /system configuration/i });
    expect(screen.queryByText('Admin PIN')).not.toBeInTheDocument();
  });

  it('requires the current PIN before changing it in the app', async () => {
    vi.spyOn(Capacitor, 'isNativePlatform').mockReturnValue(true);
    await setAdminPin('1234');
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await user.click(await screen.findByText('Admin PIN'));
    await user.type(screen.getByLabelText('Current PIN'), '9999');
    await user.type(screen.getByLabelText('New PIN'), '5678');
    await user.type(screen.getByLabelText('Confirm PIN'), '5678');
    await user.click(screen.getByRole('button', { name: /save pin/i }));

    expect(await screen.findByText('Current PIN is incorrect')).toBeInTheDocument();
    expect(await verifyAdminPin('1234')).toBe(true);

    await user.clear(screen.getByLabelText('Current PIN'));
    await user.type(screen.getByLabelText('Current PIN'), '1234');
    await user.click(screen.getByRole('button', { name: /save pin/i }));

    await waitFor(async () => expect(await verifyAdminPin('5678')).toBe(true));
  });
});
