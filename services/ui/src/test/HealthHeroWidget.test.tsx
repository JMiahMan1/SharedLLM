import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import HealthHeroWidget from '../components/widgets/HealthHeroWidget';
import { renderWithProviders } from './render';
import type { UserWidgetSettings } from '../types/widget';

const mocks = vi.hoisted(() => ({ getStepRanges: vi.fn() }));

vi.mock('../services/api', () => ({
  api: { getStepRanges: mocks.getStepRanges },
}));

const SETTINGS = {
  widget_key: 'health_hero',
  visibility: 'visible',
  order_index: 0,
  size: 'wide',
  is_pinned: false,
  sort_mode: null,
  pinned_devices: [],
  config: {},
  updated_at: 0,
} as unknown as UserWidgetSettings;

const day = (steps: number, over: Record<string, unknown> = {}) => ({
  user_id: 'jeremiah',
  range: 'D',
  range_label: 'Today',
  buckets: [{ label: 'Today', start: '2025-09-28', end: '2025-09-28', steps, days_missing: 0, days_recorded: 1, complete: true }],
  total: steps,
  daily_average: steps,
  best: null,
  baseline: null,
  baseline_days: 0,
  baseline_min_days: 7,
  thin: true,
  gaps: false,
  goal: 10000,
  days_recorded: 1,
  ...over,
});

const week = (over: Record<string, unknown> = {}) => ({
  ...day(5000, { range: 'W', thin: false, baseline: 8000, baseline_days: 30 }),
  buckets: [
    { label: 'M', start: '2025-09-22', end: '2025-09-22', steps: 8000, days_missing: 0, days_recorded: 1, complete: true },
    { label: 'T', start: '2025-09-23', end: '2025-09-23', steps: 9000, days_missing: 0, days_recorded: 1, complete: true },
  ],
  ...over,
});

describe('HealthHeroWidget', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getStepRanges.mockImplementation(async (_u: string | undefined, range: string) =>
      range === 'D' ? day(5000) : week(),
    );
  });

  it('shows today as the hero, with a 7-day window behind the insight', async () => {
    renderWithProviders(
      <HealthHeroWidget settingsButton={null} userSettings={SETTINGS} onTogglePin={() => {}} />,
    );
    expect(await screen.findByTestId('health-hero-widget')).toBeInTheDocument();
    // D for the ring, W for the baseline the insight compares against.
    // Assert the ranges rather than the user argument: the harness has no
    // signed-in user, so the username is legitimately undefined and
    // expect.anything() does not match undefined.
    const ranges = mocks.getStepRanges.mock.calls.map((call) => call[1]);
    expect(ranges).toContain('D');
    expect(ranges).toContain('W');
  });

  it('renders the one thing worth saying, reusing heroInsight', async () => {
    renderWithProviders(
      <HealthHeroWidget settingsButton={null} userSettings={SETTINGS} onTogglePin={() => {}} />,
    );
    expect(await screen.findByTestId('health-hero-insight')).toBeInTheDocument();
  });

  // An empty panel reads as a broken widget; the reason has to be visible.
  it('explains an absence instead of rendering a blank box', async () => {
    mocks.getStepRanges.mockImplementation(async (_u: string | undefined, range: string) =>
      range === 'D' ? day(0) : week({ baseline: null, thin: true }),
    );
    renderWithProviders(
      <HealthHeroWidget settingsButton={null} userSettings={SETTINGS} onTogglePin={() => {}} />,
    );
    expect(await screen.findByTestId('health-hero-no-insight')).toHaveTextContent(
      /nothing recorded yet/i,
    );
    expect(screen.queryByTestId('health-hero-insight')).not.toBeInTheDocument();
  });

  it('offers a pin control that reports whether it is already pinned', async () => {
    const onTogglePin = vi.fn();
    renderWithProviders(
      <HealthHeroWidget settingsButton={null} userSettings={SETTINGS} onTogglePin={onTogglePin} />,
    );
    const btn = await screen.findByRole('button', { name: /pin today/i });
    expect(btn).toHaveAttribute('aria-pressed', 'false');
    await btn.click();
    expect(onTogglePin).toHaveBeenCalledTimes(1);
  });

  it('reflects a pinned widget in the toggle', async () => {
    // A separate render rather than rerender(): renderWithProviders' rerender
    // re-renders the bare tree and drops the providers, so useAuth throws.
    renderWithProviders(
      <HealthHeroWidget
        settingsButton={null}
        userSettings={{ ...SETTINGS, is_pinned: true }}
        onTogglePin={() => {}}
      />,
    );
    expect(await screen.findByRole('button', { name: /unpin today/i })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('survives the step endpoints failing, rather than blanking', async () => {
    mocks.getStepRanges.mockRejectedValue(new Error('geo down'));
    renderWithProviders(
      <HealthHeroWidget settingsButton={null} userSettings={SETTINGS} onTogglePin={() => {}} />,
    );
    expect(await screen.findByTestId('health-hero-widget')).toBeInTheDocument();
  });
});
