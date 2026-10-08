import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import EnergyInsightsWidget from './EnergyInsightsWidget';
import type { UserWidgetSettings } from '../../types/widget';

vi.mock('../../services/api', async (importOriginal) => ({
  api: {
    ...(await importOriginal<typeof import('../../services/api')>()).api,
    getTelemetryEnrollments: vi.fn(),
    getTelemetrySummary: vi.fn(),
  },
}));

import { api } from '../../services/api';

const apiMock = api as unknown as {
  getTelemetryEnrollments: ReturnType<typeof vi.fn>;
  getTelemetrySummary: ReturnType<typeof vi.fn>;
};

const settings: UserWidgetSettings = {
  widget_key: 'energy_insights',
  visibility: 'visible',
  order_index: 0,
  size: 'medium',
  is_pinned: false,
};

const summary = (current: number) => ({
  entity_id: 'sensor.rosebud_123_1min',
  summary: {
    current_power_w: current,
    peak_power_w: 9.75,
    peak_at: null,
    peak_duration_seconds: null,
    avg_power_w: 1.2345678,
    availability_pct: 99.9,
    total_activations: 3,
    last_outage_at: null,
    // The per-device rows only render alongside the chart, so a reading needs
    // at least one data point for the widget to show anything at all.
    data_points: [{ recorded_at: 1_700_000_000, power_w: current }],
  },
});

function enrolment(entityId: string) {
  return {
    entity_id: entityId,
    power_tracking: true,
    availability_tracking: false,
    usage_tracking: false,
    offline_alert_threshold_minutes: 30,
  };
}

function renderWidget() {
  return render(
    <EnergyInsightsWidget settingsButton={null} userSettings={settings} onTogglePin={() => {}} />,
  );
}

describe('EnergyInsightsWidget', () => {
  beforeEach(() => {
    apiMock.getTelemetryEnrollments.mockResolvedValue([
      enrolment('sensor.rosebud_123_1min'),
      enrolment('sensor.ac_unit_1min'),
    ]);
    apiMock.getTelemetrySummary.mockImplementation(async (entityId: string) =>
      summary(entityId === 'sensor.rosebud_123_1min' ? 2.8517875717626 : 2.85),
    );
  });

  it('shows each device’s draw as a whole number of watts', async () => {
    renderWidget();
    const row = (await screen.findByText('rosebud_123_1min')).closest('div');
    expect(row).not.toBeNull();
    expect(row!.textContent).toContain('3W');
    expect(row!.textContent).not.toContain('2.8517875717626');
  });

  it('keeps the share of the total next to it', async () => {
    renderWidget();
    const row = (await screen.findByText('rosebud_123_1min')).closest('div');
    expect(row!.textContent).toContain('50%');
  });

  it('rounds every device, not only the first', async () => {
    apiMock.getTelemetrySummary.mockImplementation(async () => summary(7.4999));
    renderWidget();
    const rows = await screen.findAllByText(/^7W/);
    expect(rows.length).toBeGreaterThan(0);
    expect(screen.queryByText(/7\.4999/)).toBeNull();
  });
});
