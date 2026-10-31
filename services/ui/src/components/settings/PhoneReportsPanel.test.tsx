import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@capacitor/core', async (orig) => {
  const real = await orig<typeof import('@capacitor/core')>();
  return { ...real, Capacitor: { ...real.Capacitor, getPlatform: () => 'android' } };
});
const comms = vi.hoisted(() => ({ hasAccess: vi.fn(), requestAccess: vi.fn() }));
vi.mock('../../plugins/comms', () => ({ default: comms }));
vi.mock('../../plugins/phoneUsage', () => ({ default: { hasAccess: vi.fn().mockResolvedValue({ granted: true }), openSettings: vi.fn() } }));
vi.mock('../../plugins/mediaSession', () => ({ default: { hasAccess: vi.fn().mockResolvedValue({ granted: false }), openSettings: vi.fn() } }));

import PhoneReportsPanel from './PhoneReportsPanel';

describe('PhoneReportsPanel', () => {
  beforeEach(() => {
    comms.hasAccess.mockResolvedValue({ calls: false, sms: false });
    comms.requestAccess.mockImplementation(async () => {
      comms.hasAccess.mockResolvedValue({ calls: true, sms: true });
      return { calls: true, sms: true };
    });
  });

  it('shows what is allowed and asks for calls and texts on any phone', async () => {
    render(<PhoneReportsPanel />);
    await waitFor(() => expect(screen.getByTestId('phone-report-usage')).toHaveTextContent('Allowed'));
    expect(screen.getByTestId('phone-report-notifications')).toHaveTextContent('Allow');
    await userEvent.click(screen.getByTestId('phone-report-calls').querySelector('button')!);
    expect(comms.requestAccess).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId('phone-report-calls')).toHaveTextContent('Allowed'));
    expect(screen.getByTestId('phone-report-sms')).toHaveTextContent('Allowed');
  });
});
