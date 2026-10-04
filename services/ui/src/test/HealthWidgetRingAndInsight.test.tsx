import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import HealthActivityWidget from '../components/widgets/HealthActivityWidget';

/**
 * The dashboard previously had a second health widget (`health_hero`, labelled
 * "Today") with a baseline-aware ring and a "how am I doing" insight, while the
 * Health widget drew its own plain progress ring with no baseline. Both showed
 * steps, so the dashboard showed the same number twice under two names.
 *
 * The duplicate is gone and its good parts now live in the one Health widget: the
 * shared `ActivityRings`, so the widget and the Health page cannot drift, and the
 * `heroInsight` line. These tests pin that, because the easy regression is quietly
 * reverting to a bespoke ring or dropping the insight to save space.
 */

const nowSeconds = () => Date.now() / 1000;
const today = () => new Date().toISOString().slice(0, 10);

function stepsPayload(steps: number, lastSyncedSecondsAgo = 60) {
  return {
    user_id: 'default',
    days: 7,
    daily_steps: { [today()]: steps },
    today: steps,
    goal: 10000,
    sources: { phone: steps },
    last_synced: nowSeconds() - lastSyncedSecondsAgo,
  };
}

/** `heroInsight` returns null for an empty bucket list, so these are real. */
function rangesPayload(baseline: number | null, thin: boolean, daysRecorded: number) {
  return {
    user_id: 'default',
    range: 'W',
    label: 'Last 7 days',
    buckets: [
      { label: 'D1', start: today(), end: today(), steps: 9100, days_missing: 0, days_recorded: 1, complete: true },
      { label: 'D2', start: today(), end: today(), steps: 6400, days_missing: 0, days_recorded: 1, complete: true },
    ],
    total: 15500,
    daily_average: 7600,
    days_recorded: daysRecorded,
    goal: 10000,
    baseline,
    baseline_min_days: 7,
    thin,
    has_gaps: false,
    best: { label: 'Best', steps: 9100 },
  };
}

function mock(steps: number, opts: { baseline?: number | null; thin?: boolean; days?: number; syncedAgo?: number } = {}) {
  server.use(
    http.get('/api/geo/steps', () => HttpResponse.json(stepsPayload(steps, opts.syncedAgo ?? 60))),
    http.get('/api/geo/steps/ranges', () =>
      HttpResponse.json(rangesPayload(opts.baseline ?? null, opts.thin ?? false, opts.days ?? 7))
    ),
    // The widget publishes its resolved theme colours for the native Android
    // widget; without a handler msw errors on every render.
    http.put('/api/widgets/settings/:key', () => HttpResponse.json({ status: 'SUCCESS' }))
  );
}

function render() {
  return renderWithProviders(
    <HealthActivityWidget
      settingsButton={null}
      onTogglePin={() => undefined}
      userSettings={{ config: {}, widget_key: 'health_activity' } as never}
    />
  );
}

describe('HealthActivityWidget ring and insight', () => {
  beforeEach(() => {
    server.resetHandlers();
  });

  it('uses the shared ActivityRings rather than a bespoke ring', async () => {
    mock(7330, { baseline: 7600 });
    render();

    // The single implementation both surfaces share.
    expect(await screen.findByTestId('activity-rings')).toBeInTheDocument();
    expect(await screen.findByTestId('ring-steps')).toBeInTheDocument();
  });

  it('states the baseline zone for the day', async () => {
    // 4200 against a personal median of 8000 is 0.525 of usual: the 'low' band.
    mock(4200, { baseline: 8000 });
    render();

    await waitFor(() =>
      expect(screen.getByTestId('ring-zone-steps')).toHaveTextContent('Below usual')
    );
  });

  it('does not claim the usual pace when the day is far below it', async () => {
    // Guards the bug this change could have introduced: pinning `tone` overrides
    // the zone outright, so every day would read "On your usual pace".
    mock(500, { baseline: 9000 });
    render();

    await waitFor(() =>
      expect(screen.getByTestId('ring-zone-steps')).toHaveTextContent('Well under usual')
    );
  });

  it('shows the baseline-relative insight', async () => {
    mock(4200, { baseline: 8000 });
    render();

    // This is the sentence the duplicate widget existed for; it must not be lost.
    expect(await screen.findByTestId('health-activity-insight')).toBeInTheDocument();
  });

  it('says why there is no insight instead of showing a blank', async () => {
    mock(0, { baseline: null, thin: true, days: 2 });
    render();

    const fallback = await screen.findByTestId('health-activity-no-insight');
    expect(fallback).toHaveTextContent(/nothing recorded|not enough history/i);
  });

  it('explains a thin baseline rather than claiming a comparison', async () => {
    mock(5000, { baseline: null, thin: true, days: 2 });
    render();

    // `thin` means too little history for the zone to mean anything. The widget
    // turns the ring's own note off (it would be a second wrapped line in a small
    // cell) and says it in the insight line instead — so assert the message
    // appears *somewhere* rather than in a component the widget opted out of.
    await waitFor(() =>
      expect(screen.getByText(/not enough history|needs 7 days/i)).toBeInTheDocument()
    );
  });

  it('keeps the freshness line, so a frozen number is still distinguishable', async () => {
    mock(7330, { baseline: 7600, syncedAgo: 7200 });
    render();

    // Two hours ago: the ring and insight may be right, but the card must say so.
    await waitFor(() =>
      expect(screen.getByTestId('health-sync-status')).toHaveAttribute('data-freshness')
    );
  });

  it('still shows the step total against the goal', async () => {
    mock(7330, { baseline: 7600 });
    render();

    // The ring centre prints the total as well as the card header, so the number
    // appears more than once — assert presence, not uniqueness.
    await waitFor(() => expect(screen.getAllByText('7,330').length).toBeGreaterThanOrEqual(1));
    expect(screen.getByText(/10,000/)).toBeInTheDocument();
  });
});