import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const mocks = vi.hoisted(() => ({
  isBatteryUnrestricted: vi.fn(),
  requestBatteryUnrestricted: vi.fn(async () => undefined),
}));

vi.mock('../lib/locationTracking', () => mocks);

import BatteryUnrestrictedNotice from '../components/location/BatteryUnrestrictedNotice';

describe('BatteryUnrestrictedNotice', () => {
  beforeEach(() => {
    mocks.isBatteryUnrestricted.mockReset();
    mocks.requestBatteryUnrestricted.mockClear();
  });

  it('asks for the exemption while battery optimisation restricts the app', async () => {
    mocks.isBatteryUnrestricted.mockResolvedValue(false);
    render(<BatteryUnrestrictedNotice />);
    fireEvent.click(await screen.findByRole('button', { name: /allow in background/i }));
    expect(mocks.requestBatteryUnrestricted).toHaveBeenCalledTimes(1);
  });

  it('goes away once allowed, checked again on return to the app', async () => {
    mocks.isBatteryUnrestricted.mockResolvedValueOnce(false).mockResolvedValue(true);
    render(<BatteryUnrestrictedNotice />);
    await screen.findByRole('button', { name: /allow in background/i });
    document.dispatchEvent(new Event('visibilitychange'));
    await waitFor(() => expect(screen.queryByRole('button', { name: /allow in background/i })).toBeNull());
  });

  it('shows nothing when it cannot tell (web, or an older APK)', async () => {
    mocks.isBatteryUnrestricted.mockResolvedValue(null);
    const { container } = render(<BatteryUnrestrictedNotice />);
    await waitFor(() => expect(mocks.isBatteryUnrestricted).toHaveBeenCalled());
    expect(container.innerHTML).toBe('');
  });
});
