import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import OfflineCodeSetting from './OfflineCodeSetting';
import { api } from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), getOfflineCode: vi.fn(), setOfflineCode: vi.fn(), clearOfflineCode: vi.fn() },
  };
});

const wrap = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <OfflineCodeSetting />
    </QueryClientProvider>,
  );

describe('OfflineCodeSetting', () => {
  beforeEach(() => vi.clearAllMocks());

  it('sets a code of 6 to 12 digits and never shows it back', async () => {
    vi.mocked(api.getOfflineCode).mockResolvedValue({ set: false, updated_at: null, can_set: true });
    vi.mocked(api.setOfflineCode).mockResolvedValue({ set: true, updated_at: 'x' });
    wrap();
    const input = await screen.findByLabelText('New offline unlock code');
    const button = screen.getByRole('button', { name: 'Set code' });
    fireEvent.change(input, { target: { value: '12a3' } });
    expect(button).toBeDisabled();
    fireEvent.change(input, { target: { value: '48291735' } });
    fireEvent.click(button);
    await waitFor(() => expect(api.setOfflineCode).toHaveBeenCalledWith('48291735'));
    await waitFor(() => expect(input).toHaveValue(''));
  });

  it('is hidden from someone who is not a parent', async () => {
    vi.mocked(api.getOfflineCode).mockResolvedValue({ set: false, updated_at: null, can_set: false });
    const { container } = wrap();
    await waitFor(() => expect(api.getOfflineCode).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('removes a code', async () => {
    vi.mocked(api.getOfflineCode).mockResolvedValue({ set: true, updated_at: 'x', can_set: true });
    vi.mocked(api.clearOfflineCode).mockResolvedValue({ set: false });
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(api.clearOfflineCode).toHaveBeenCalled());
  });
});
