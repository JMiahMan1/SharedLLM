import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import FindPhoneCard from './FindPhoneCard';
import { api } from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), ringMemberPhone: vi.fn() },
  };
});

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <FindPhoneCard username="emma" name="Emma" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('FindPhoneCard', () => {
  it('rings the child\'s phone and confirms', async () => {
    vi.mocked(api.ringMemberPhone).mockResolvedValue({ status: 'SUCCESS', message: 'The phone is ringing.' });
    renderCard();

    fireEvent.click(screen.getByTestId('member-ring-phone'));

    await waitFor(() => expect(api.ringMemberPhone).toHaveBeenCalledWith('emma'));
    expect(await screen.findByTestId('member-find-phone-message')).toHaveTextContent('The phone is ringing.');
  });

  it('shows why a ring did not go through', async () => {
    vi.mocked(api.ringMemberPhone).mockRejectedValue(new Error('That phone is already ringing.'));
    renderCard();

    fireEvent.click(screen.getByTestId('member-ring-phone'));

    expect(await screen.findByTestId('member-find-phone-message')).toHaveTextContent('That phone is already ringing.');
  });
});
