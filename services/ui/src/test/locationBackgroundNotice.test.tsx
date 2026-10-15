import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const mocks = vi.hoisted(() => ({
  isBatteryUnrestricted: vi.fn(),
  requestBatteryUnrestricted: vi.fn(async () => undefined),
  hasBackgroundLocation: vi.fn(),
  requestBackgroundLocation: vi.fn(async () => undefined),
}));

vi.mock('../lib/locationTracking', () => mocks);

import LocationBackgroundNotice from '../components/location/LocationBackgroundNotice';

describe('LocationBackgroundNotice', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mocks.hasBackgroundLocation.mockResolvedValue(true);
    mocks.isBatteryUnrestricted.mockResolvedValue(true);
  });

  it('asks for "Allow all the time" first: without it nothing reports once the app closes', async () => {
    mocks.hasBackgroundLocation.mockResolvedValue(false);
    mocks.isBatteryUnrestricted.mockResolvedValue(false);
    render(<LocationBackgroundNotice />);
    fireEvent.click(await screen.findByRole('button', { name: /allow all the time/i }));
    expect(mocks.requestBackgroundLocation).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: /allow in background/i })).toBeNull();
  });

  it('then asks for the battery exemption', async () => {
    mocks.isBatteryUnrestricted.mockResolvedValue(false);
    render(<LocationBackgroundNotice />);
    fireEvent.click(await screen.findByRole('button', { name: /allow in background/i }));
    expect(mocks.requestBatteryUnrestricted).toHaveBeenCalledTimes(1);
  });

  it("goes away once Android's screen was used, checked again on return", async () => {
    mocks.hasBackgroundLocation.mockResolvedValueOnce(false).mockResolvedValue(true);
    render(<LocationBackgroundNotice />);
    await screen.findByRole('button', { name: /allow all the time/i });
    document.dispatchEvent(new Event('visibilitychange'));
    await waitFor(() => expect(screen.queryByTestId('location-background-notice')).toBeNull());
  });

  it('shows nothing when it cannot tell (web, or an older APK)', async () => {
    mocks.hasBackgroundLocation.mockResolvedValue(null);
    mocks.isBatteryUnrestricted.mockResolvedValue(null);
    const { container } = render(<LocationBackgroundNotice />);
    await waitFor(() => expect(mocks.hasBackgroundLocation).toHaveBeenCalled());
    expect(container.innerHTML).toBe('');
  });
});
