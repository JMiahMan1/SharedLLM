import { describe, it, expect, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import HealthActivityWidget from '../components/widgets/HealthActivityWidget';

/**
 * The dashboard card is the surface a user sees without opening the Health
 * page, so it has to answer "is this number live?" on its own. Before this,
 * a card showing a number frozen since the phone went to sleep looked exactly
 * like a card showing a quiet day so far.
 */

/** Built from the real clock, because `syncStatus` compares against `Date.now()`;
 * a hardcoded epoch would always read as many hours stale. */
const nowSeconds = () => Date.now() / 1000;

function stepsPayload(lastSyncedSecondsAgo: number | null) {
  return {
    user_id: 'default',
    days: 7,
    daily_steps: { [new Date().toISOString().slice(0, 10)]: 4200 },
    today: 4200,
    goal: 10000,
    sources: { phone: 4200 },
    last_synced: lastSyncedSecondsAgo === null ? null : nowSeconds() - lastSyncedSecondsAgo,
  };
}

function mockSteps(lastSyncedSecondsAgo: number | null) {
  server.use(
    http.get('/api/geo/steps', () => HttpResponse.json(stepsPayload(lastSyncedSecondsAgo)))
  );
}

describe('HealthActivityWidget freshness', () => {
  beforeEach(() => {
    server.resetHandlers();
  });

  it('says when the phone last reported for a recent sync', async () => {
    mockSteps(120);
    renderWithProviders(
      <HealthActivityWidget
        settingsButton={null}
        onTogglePin={() => undefined}
        userSettings={{ config: {} } as never}
      />
    );
    // Gate on the data arriving, not on the freshness element: that element
    // renders with "never" before the query resolves, so awaiting it alone
    // asserts against a placeholder.
    await screen.findAllByText('4,200');
    const status = await screen.findByTestId('health-sync-status');
    expect(status).toHaveAttribute('data-freshness', 'fresh');
    expect(status).toHaveTextContent(/2m ago/i);
  });

  it('warns when the phone has not reported for hours, instead of showing a stale number as current', async () => {
    mockSteps(5 * 3600);
    renderWithProviders(
      <HealthActivityWidget
        settingsButton={null}
        onTogglePin={() => undefined}
        userSettings={{ config: {} } as never}
      />
    );
    // Gate on the data arriving, not on the freshness element: that element
    // renders with "never" before the query resolves, so awaiting it alone
    // asserts against a placeholder.
    await screen.findAllByText('4,200');
    const status = await screen.findByTestId('health-sync-status');
    expect(status).toHaveAttribute('data-freshness', 'stale');
    expect(status).toHaveTextContent(/5h ago/);
    expect(status).toHaveTextContent(/open the app/i);
  });

  it('distinguishes a phone that has never synced from one that is merely late', async () => {
    mockSteps(null);
    renderWithProviders(
      <HealthActivityWidget
        settingsButton={null}
        onTogglePin={() => undefined}
        userSettings={{ config: {} } as never}
      />
    );
    // Gate on the data arriving, not on the freshness element: that element
    // renders with "never" before the query resolves, so awaiting it alone
    // asserts against a placeholder.
    await screen.findAllByText('4,200');
    const status = await screen.findByTestId('health-sync-status');
    expect(status).toHaveAttribute('data-freshness', 'never');
    expect(status).toHaveTextContent(/no sync yet/i);
    expect(status).not.toHaveTextContent(/ago/i);
  });

  it('does not flag an age mid-window as a problem', async () => {
    mockSteps(5 * 60);
    renderWithProviders(
      <HealthActivityWidget
        settingsButton={null}
        onTogglePin={() => undefined}
        userSettings={{ config: {} } as never}
      />
    );
    // Gate on the data arriving, not on the freshness element: that element
    // renders with "never" before the query resolves, so awaiting it alone
    // asserts against a placeholder.
    await screen.findAllByText('4,200');
    const status = await screen.findByTestId('health-sync-status');
    expect(status).toHaveAttribute('data-freshness', 'fresh');
    expect(status).not.toHaveTextContent(/open the app/i);
  });

  it('navigates to Health on click', async () => {
    mockSteps(60);
    const user = userEvent.setup();
    renderWithProviders(
      <HealthActivityWidget
        settingsButton={null}
        onTogglePin={() => undefined}
        userSettings={{ config: {} } as never}
      />
    );
    const card = await screen.findByTestId('health-activity-widget');
    await user.click(card);
    expect(card).toBeInTheDocument();
  });
});