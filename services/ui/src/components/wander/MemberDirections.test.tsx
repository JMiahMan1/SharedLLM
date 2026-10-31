import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import MemberDirections from './MemberDirections';
import { api } from '../../services/api';

vi.mock('../../services/api', async (orig) => {
  const actual = (await orig()) as { api: Record<string, unknown> };
  return { api: { ...actual.api, getEta: vi.fn() } };
});

const renderIt = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemberDirections me="dad" name="Leo" lat={33.312345} lon={-111.6} />
    </QueryClientProvider>,
  );

describe('MemberDirections', () => {
  it('links to driving directions and shows the drive time from you', async () => {
    vi.mocked(api.getEta).mockResolvedValue({ arrived: false, duration_s: 1500 } as never);
    renderIt();
    const link = screen.getByRole('link', { name: 'Directions to Leo' });
    expect(link).toHaveAttribute('href', 'https://www.google.com/maps/dir/?api=1&destination=33.312345,-111.600000&travelmode=driving');
    expect(await screen.findByText(/25 min away by car/)).toBeInTheDocument();
    expect(api.getEta).toHaveBeenCalledWith('dad', '33.312,-111.600');
  });

  it('still offers directions when no drive time can be worked out', async () => {
    vi.mocked(api.getEta).mockRejectedValue(new Error('no fix'));
    renderIt();
    expect(screen.getByRole('link', { name: 'Directions to Leo' })).toBeInTheDocument();
    expect(screen.getByTestId('member-drive-time')).toBeEmptyDOMElement();
  });
});
