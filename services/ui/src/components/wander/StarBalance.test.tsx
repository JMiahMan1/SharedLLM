import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import StarBalance from './StarBalance';
import { api } from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), getStars: vi.fn(), grantStars: vi.fn() },
  };
});

const getStars = vi.mocked(api.getStars);
const grantStars = vi.mocked(api.grantStars);

function renderStars(userId = 'mom') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <StarBalance userId={userId} />
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
