import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import DevicesSection from './DevicesSection';
import { api } from '../../../services/api';

vi.mock('../../ui/confirmDialog', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }));
vi.mock('../../../services/api', async (orig) => {
  const actual = (await orig()) as { api: Record<string, unknown> };
  return { api: { ...actual.api, getMemberDevices: vi.fn(), clearMemberTamper: vi.fn() } };
});

const report = (i: number) => ({ id: i, event: 'guard_tamper', at: new Date(Date.now() - i * 60_000).toISOString(), extra: { event: 'guard_disabled' }, device_key: null });

function setup(tamper: ReturnType<typeof report>[]) {
  vi.mocked(api.getMemberDevices).mockResolvedValue({
    username: 'kid',
    devices: [{ device_key: 'kid-phone-0000000001', kind: 'phone', capabilities: {} }],
    tamper,
    offline_code: {},
  } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DevicesSection username="kid" name="Kid" />
    </QueryClientProvider>,
  );
}

describe('tamper reports', () => {
  beforeEach(() => vi.mocked(api.clearMemberTamper).mockReset());

  it('shows the latest five, all on request, and clears them', async () => {
    setup(Array.from({ length: 12 }, (_, i) => report(i)));
    const card = await screen.findByTestId('member-tamper');
    expect(within(card).getAllByRole('listitem')).toHaveLength(5);
    await userEvent.click(within(card).getByTestId('member-tamper-more'));
    expect(within(card).getAllByRole('listitem')).toHaveLength(12);

    vi.mocked(api.clearMemberTamper).mockResolvedValue({ cleared: 12 });
    vi.mocked(api.getMemberDevices).mockResolvedValue({ username: 'kid', devices: [{ device_key: 'kid-phone-0000000001', kind: 'phone', capabilities: {} }], tamper: [], offline_code: {} } as never);
    await userEvent.click(within(card).getByTestId('member-tamper-clear'));
    expect(api.clearMemberTamper).toHaveBeenCalledWith('kid');
    await waitFor(() => expect(screen.getByTestId('member-tamper-empty')).toBeInTheDocument());
  });

  it('has nothing to clear when there are no reports', async () => {
    setup([]);
    await screen.findByTestId('member-tamper-empty');
    expect(screen.queryByTestId('member-tamper-clear')).toBeNull();
  });
});
