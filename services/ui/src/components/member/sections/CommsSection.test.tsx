import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import CommsSection from './CommsSection';
import { api } from '../../../services/api';

vi.mock('../../../services/api', async (orig) => {
  const actual = (await orig()) as { api: Record<string, unknown> };
  return { api: { ...actual.api, getGuardianComms: vi.fn() } };
});

const now = Date.now();
const at = (minAgo: number) => new Date(now - minAgo * 60_000).toISOString();
const EVENTS = [
  { user: 'gracie', feature: 'sms', item: 'Leo', action: 'message', at: at(5), detail: { direction: 'in', number: '+15555550102', body: 'Are you coming to youth group tonight? ' + 'x'.repeat(300) } },
  { user: 'gracie', feature: 'calls', item: 'Mom', action: 'call', at: at(30), duration_s: 252, detail: { direction: 'out', number: '+15555550101' } },
  { user: 'gracie', feature: 'calls', item: 'Unknown', action: 'call', at: at(40), duration_s: 0, detail: { direction: 'missed', number: '+15555550199' } },
  { user: 'gracie', feature: 'social', item: 'Snapchat', action: 'notification', at: at(50), detail: { sender: 'Ava', body: 'lol see you' } },
  { user: 'gracie', feature: 'sms', item: 'Leo', action: 'message', at: at(60), detail: { direction: 'out', number: '+15555550102', body: 'ok' } },
];

const renderIt = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <CommsSection username="gracie" name="Gracie" />
    </QueryClientProvider>,
  );

describe('CommsSection', () => {
  it('gives a parent the totals, who she talks with most, and every call and message', async () => {
    vi.mocked(api.getGuardianComms).mockResolvedValue({ days: 7, events: EVENTS } as never);
    renderIt();
    const totals = await screen.findByTestId('member-comms-totals');
    expect(totals).toHaveTextContent('Calls2');
    expect(totals).toHaveTextContent('1 missed');
    expect(totals).toHaveTextContent('Talk time4 min');
    expect(totals).toHaveTextContent('1 sent · 1 received');
    expect(totals).toHaveTextContent('App messages1');
    expect(screen.getByTestId('member-comms-contacts')).toHaveTextContent('Leo · 2 texts');
    const items = screen.getAllByTestId('member-comms-item');
    expect(items).toHaveLength(5);
    expect(items[0]).toHaveTextContent('Text from Leo');
    expect(items[1]).toHaveTextContent('Call to Mom4 min 12 s');
    expect(items[3]).toHaveTextContent('Ava on Snapchat');
  });

  it('filters by kind and searches names and message text', async () => {
    vi.mocked(api.getGuardianComms).mockResolvedValue({ days: 7, events: EVENTS } as never);
    renderIt();
    await screen.findByTestId('member-comms-list');
    await userEvent.click(within(screen.getByRole('group', { name: 'Show' })).getByRole('button', { name: 'Calls' }));
    expect(screen.getAllByTestId('member-comms-item')).toHaveLength(2);
    await userEvent.click(within(screen.getByRole('group', { name: 'Show' })).getByRole('button', { name: 'All' }));
    await userEvent.type(screen.getByLabelText('Search calls and messages'), 'youth group');
    expect(screen.getAllByTestId('member-comms-item')).toHaveLength(1);
  });

  it('opens a message to its whole text', async () => {
    vi.mocked(api.getGuardianComms).mockResolvedValue({ days: 7, events: EVENTS } as never);
    renderIt();
    const first = (await screen.findAllByTestId('member-comms-item'))[0];
    const body = within(first).getByRole('button', { expanded: false });
    await userEvent.click(body);
    expect(within(first).getByRole('button', { expanded: true })).toHaveTextContent('x'.repeat(300));
  });

  it('tells a non-parent plainly', async () => {
    vi.mocked(api.getGuardianComms).mockRejectedValue(Object.assign(new Error('no'), { isAxiosError: true, response: { status: 403 } }));
    renderIt();
    await waitFor(() => expect(screen.getByTestId('member-comms-error')).toHaveTextContent('Only a parent can see calls and messages.'));
  });
});
