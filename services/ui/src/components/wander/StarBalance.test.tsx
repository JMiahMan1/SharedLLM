import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import StarBalance from './StarBalance';
import { api } from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getStars: vi.fn(),
      grantStars: vi.fn(),
      grantStarsForUser: vi.fn(),
    },
  };
});

const getStars = vi.mocked(api.getStars);
const grantStars = vi.mocked(api.grantStars);
const grantStarsForUser = vi.mocked(api.grantStarsForUser);

function renderStars(userId = 'mom', admin = false) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <StarBalance userId={userId} admin={admin} />
    </QueryClientProvider>,
  );
}

describe('StarBalance', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getStars.mockResolvedValue({ user_id: 'mom', stars: 4, grants: [] });
  });

  it('shows the current balance', async () => {
    renderStars();
    await waitFor(() => expect(screen.getByTestId('star-balance')).toBeTruthy());
    expect(screen.getByText('4')).toBeTruthy();
  });

  it('grants stars with the chosen reason and note', async () => {
    grantStars.mockResolvedValue({
      stars: 2,
      balance: 6,
      reason: 'game',
      note: 'won the quiz',
      granted_by: 'dad',
      at: 1,
    });
    renderStars();

    await waitFor(() => expect(screen.getByTestId('star-balance')).toBeTruthy());
    fireEvent.change(screen.getByLabelText('Star amount'), { target: { value: '2' } });
    fireEvent.change(screen.getByLabelText('Star reason'), { target: { value: 'game' } });
    fireEvent.change(screen.getByLabelText('Star note'), { target: { value: 'won the quiz' } });
    fireEvent.click(screen.getByRole('button', { name: /grant/i }));

    await waitFor(() =>
      expect(grantStars).toHaveBeenCalledWith({
        user_id: 'mom',
        stars: 2,
        reason: 'game',
        note: 'won the quiz',
      }),
    );
  });

  it('refuses a zero grant without calling the API', async () => {
    renderStars();
    await waitFor(() => expect(screen.getByTestId('star-balance')).toBeTruthy());

    fireEvent.change(screen.getByLabelText('Star amount'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: /grant/i }));

    expect(grantStars).not.toHaveBeenCalled();
  });

  it('is disabled for the family-wide view', async () => {
    renderStars('all');
    await waitFor(() => expect(screen.getByTestId('star-balance')).toBeTruthy());
    expect(screen.getByRole('button', { name: /grant/i }).hasAttribute('disabled')).toBe(true);
  });

  it('lists the recent ledger', async () => {
    getStars.mockResolvedValue({
      user_id: 'mom',
      stars: 4,
      grants: [
        { stars: 2, balance: 4, reason: 'game', note: 'won the quiz', granted_by: 'dad', at: 2 },
        { stars: -1, balance: 2, reason: 'manual', note: 'took my cookie', granted_by: 'kiddo', at: 1 },
      ],
    });
    renderStars();

    await waitFor(() => expect(screen.getByTestId('star-ledger')).toBeTruthy());
    const ledger = screen.getByTestId('star-ledger');
    expect(ledger.textContent).toContain('+2 · game · won the quiz · dad');
    expect(ledger.textContent).toContain('-1 · manual · took my cookie · kiddo');
  });
});

/**
 * The admin surface grants through the route that also mirrors to Skylight.
 *
 * The behaviour worth pinning is that a mirror failure is *visible*: the ledger
 * write succeeded and the award must stand, but the admin has to be told the
 * second half did not happen, and told that re-granting is the wrong fix.
 */
describe('StarBalance on the admin surface', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getStars.mockResolvedValue({ user_id: 'mom', stars: 4, grants: [] });
    grantStarsForUser.mockResolvedValue({
      user_id: 'mom',
      ledger: { stars: 2, balance: 6, reason: 'bonus', granted_by: 'admin', at: 1 },
      skylight: { status: 'SUCCESS', message: 'Granted 2 star(s) to mom' },
    });
  });

  it('grants through the mirroring route with the recipient in the path', async () => {
    renderStars('mom', true);
    await waitFor(() => expect(screen.getByTestId('star-balance')).toBeTruthy());

    fireEvent.change(screen.getByLabelText('Star amount'), { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: /grant/i }));

    await waitFor(() =>
      expect(grantStarsForUser).toHaveBeenCalledWith('mom', {
        stars: 2,
        reason: 'bonus',
        note: undefined,
        mirror_to_skylight: true,
      }),
    );
    // The body-carried-user_id route must not be used here: two ways to name a
    // recipient means a grant that could land on the wrong person.
    expect(grantStars).not.toHaveBeenCalled();
  });

  it('can be told not to mirror, so a double award is a deliberate choice', async () => {
    renderStars('mom', true);
    await waitFor(() => expect(screen.getByTestId('star-balance')).toBeTruthy());

    fireEvent.click(screen.getByRole('checkbox', { name: /mirror into mom/i }));
    fireEvent.click(screen.getByRole('button', { name: /grant/i }));

    await waitFor(() =>
      expect(grantStarsForUser).toHaveBeenCalledWith(
        'mom',
        expect.objectContaining({ mirror_to_skylight: false }),
      ),
    );
  });

  it('reports a mirror failure without pretending the grant failed', async () => {
    grantStarsForUser.mockResolvedValue({
      user_id: 'mom',
      ledger: { stars: 2, balance: 6, reason: 'bonus', granted_by: 'admin', at: 1 },
      skylight: { status: 'FAILURE', message: 'Skylight is not configured' },
    });
    renderStars('mom', true);
    await waitFor(() => expect(screen.getByTestId('star-balance')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: /grant/i }));

    const outcome = await screen.findByTestId('star-mirror-outcome');
    expect(outcome.getAttribute('data-status')).toBe('FAILURE');
    expect(outcome.textContent).toContain('Recorded in the ledger');
    expect(outcome.textContent).toContain('Skylight is not configured');
    // The one dangerous misreading: granting again would double-count.
    expect(outcome.textContent).toMatch(/do not grant again/i);
  });

  it('distinguishes a skipped mirror from a delivered one', async () => {
    grantStarsForUser.mockResolvedValue({
      user_id: 'mom',
      ledger: { stars: 2, balance: 6, reason: 'bonus', granted_by: 'admin', at: 1 },
      skylight: { status: 'SKIPPED', message: 'mirror_to_skylight was false' },
    });
    renderStars('mom', true);
    await waitFor(() => expect(screen.getByTestId('star-balance')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: /grant/i }));

    const outcome = await screen.findByTestId('star-mirror-outcome');
    expect(outcome.getAttribute('data-status')).toBe('SKIPPED');
    expect(outcome.textContent).toContain('not mirrored');
  });

  it('says nothing about Skylight on a non-admin surface', async () => {
    renderStars('mom', false);
    await waitFor(() => expect(screen.getByTestId('star-balance')).toBeTruthy());

    expect(screen.queryByRole('checkbox', { name: /mirror into/i })).toBeNull();
  });
});
