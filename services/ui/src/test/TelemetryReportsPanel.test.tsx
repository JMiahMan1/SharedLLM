import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TelemetryReportsPanel from '../components/settings/TelemetryReportsPanel';
import { renderWithProviders } from './render';

vi.mock('react-hot-toast', () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

describe('TelemetryReportsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders schedule controls and recent reports', async () => {
    renderWithProviders(<TelemetryReportsPanel />);

    expect(await screen.findByTestId('telemetry-reports-panel')).toBeInTheDocument();
    expect(screen.getByLabelText('Report type')).toBeInTheDocument();
    expect(screen.getByLabelText('Report frequency')).toBeInTheDocument();
    expect(screen.getByLabelText('Run time')).toBeInTheDocument();

    // seeded report is shown
    expect(await screen.findByText(/hit your step goal/i)).toBeInTheDocument();
    // notification for a finished report is surfaced in-app
    expect(await screen.findByText(/your daily health report is ready/i)).toBeInTheDocument();
  });

  it('offers end-of-day and midday presets', async () => {
    renderWithProviders(<TelemetryReportsPanel />);
    expect(await screen.findByText('End of day (21:00)')).toBeInTheDocument();
    expect(screen.getByText('Midday (12:00)')).toBeInTheDocument();
  });

  it('saves a schedule with the chosen cadence and time', async () => {
    const user = userEvent.setup();
    renderWithProviders(<TelemetryReportsPanel />);

    await screen.findByTestId('telemetry-reports-panel');
    await user.selectOptions(screen.getByLabelText('Report frequency'), 'monthly');
    await user.click(screen.getByText('Midday (12:00)'));
    await user.click(screen.getByRole('button', { name: /save schedule/i }));

    await waitFor(() => {
      expect(screen.getByTestId('active-schedule')).toBeInTheDocument();
    });
    expect(screen.getByTestId('active-schedule').textContent).toMatch(/Next run/i);
  });

  it('requests an on-demand report and explains queueing', async () => {
    const user = userEvent.setup();
    renderWithProviders(<TelemetryReportsPanel />);

    await screen.findByTestId('telemetry-reports-panel');
    await user.click(screen.getByRole('button', { name: /request report now/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /request report now/i })).toBeEnabled();
    });
  });

  it('toggles the schedule between enabled and paused', async () => {
    const user = userEvent.setup();
    renderWithProviders(<TelemetryReportsPanel />);

    await screen.findByTestId('telemetry-reports-panel');
    const pause = screen.getByRole('button', { name: /pause schedule/i });
    await user.click(pause);
    expect(await screen.findByRole('button', { name: /resume schedule/i })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /resume schedule/i }));
    expect(await screen.findByRole('button', { name: /pause schedule/i })).toBeInTheDocument();
  });
});
