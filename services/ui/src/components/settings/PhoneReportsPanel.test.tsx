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
const photos = vi.hoisted(() => ({ hasAccess: vi.fn(), requestAccess: vi.fn() }));
vi.mock('../../plugins/photoScan', () => ({ default: photos }));
const activity = vi.hoisted(() => ({ hasActivityAccess: vi.fn(), requestActivityAccess: vi.fn() }));
vi.mock('../../lib/locationTracking', () => activity);

import PhoneReportsPanel from './PhoneReportsPanel';

describe('PhoneReportsPanel', () => {
  beforeEach(() => {
    activity.hasActivityAccess.mockResolvedValue(false);
    activity.requestActivityAccess.mockImplementation(async () => {
      activity.hasActivityAccess.mockResolvedValue(true);
      return true;
    });
    comms.hasAccess.mockResolvedValue({ calls: false, sms: false });
    photos.hasAccess.mockResolvedValue({ images: false, videos: false });
    photos.requestAccess.mockImplementation(async () => {
      photos.hasAccess.mockResolvedValue({ images: true, videos: true });
      return { images: true, videos: true };
    });
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

  it('asks for photos and videos, which the safety scanner reads', async () => {
    render(<PhoneReportsPanel />);
    const row = await screen.findByTestId('phone-report-photos');
    await userEvent.click(row.querySelector('button')!);
    expect(photos.requestAccess).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId('phone-report-photos')).toHaveTextContent('Allowed'));
  });

  it('asks for physical activity, which tells it when you are driving', async () => {
    render(<PhoneReportsPanel />);
    const row = await screen.findByTestId('phone-report-activity');
    await userEvent.click(row.querySelector('button')!);
    expect(activity.requestActivityAccess).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId('phone-report-activity')).toHaveTextContent('Allowed'));
  });

  it('has no physical activity row on an app build that cannot ask', async () => {
    activity.hasActivityAccess.mockResolvedValue(null);
    render(<PhoneReportsPanel />);
    await screen.findByTestId('phone-report-usage');
    expect(screen.queryByTestId('phone-report-activity')).toBeNull();
  });
});
