import { beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import RequestsCard from './RequestsCard';
import { grantMinutes } from '../../lib/timeGrant';
import { renderWithProviders } from '../../test/render';
import { server } from '../../test/setup';
import type { ScreenTimeRequest } from '../../types/api';

function asked(over: Partial<ScreenTimeRequest> = {}): ScreenTimeRequest {
  return {
    id: 22, username: 'kid', day: '2026-11-21', minutes: 60, note: 'one more level', status: 'pending',
    granted_minutes: 0, created_at: new Date(Date.now() - 60_000).toISOString(),
    ...over,
  } as ScreenTimeRequest;
}

let pending: ScreenTimeRequest[] = [];
let decided: Array<{ id: string; body: unknown }> = [];

beforeEach(() => {
  pending = [asked()];
  decided = [];
  server.use(
    http.get('*/api/screen-time/requests', ({ request }) =>
      HttpResponse.json({ requests: new URL(request.url).searchParams.get('status') === 'pending' ? pending : [] })),
    http.post('*/api/screen-time/requests/:id', async ({ params, request }) => {
      const body = (await request.json()) as { grant: boolean; minutes?: number };
      decided.push({ id: String(params.id), body });
      pending = pending.filter((r) => String(r.id) !== String(params.id));
      return HttpResponse.json({ request: { ...asked(), status: body.grant ? 'granted' : 'declined', granted_minutes: body.minutes ?? 0 } });
    }),
  );
});

async function row() {
  return screen.findByTestId('request-time-22');
}

describe('RequestsCard: a request for more time', () => {
  it('starts at the minutes asked and approves them', async () => {
    renderWithProviders(<RequestsCard username="kid" name="Kid" kinds={['time']} />);
    const r = await row();
    expect(within(r).getByLabelText('Give')).toHaveValue(60);
    expect(r).toHaveTextContent('What they asked for.');
    fireEvent.click(within(r).getByRole('button', { name: 'Approve 60 min' }));
    await waitFor(() => expect(decided).toEqual([{ id: '22', body: { grant: true, minutes: 60 } }]));
  });

  it('lets a parent edit 60 down to 30 and approve that', async () => {
    renderWithProviders(<RequestsCard username="kid" name="Kid" kinds={['time']} />);
    const r = await row();
    fireEvent.change(within(r).getByLabelText('Give'), { target: { value: '30' } });
    expect(r).toHaveTextContent('They asked for 60.');
    fireEvent.click(within(r).getByRole('button', { name: 'Approve 30 min' }));
    await waitFor(() => expect(decided).toEqual([{ id: '22', body: { grant: true, minutes: 30 } }]));
  });

  it('has quick picks, including more than asked', async () => {
    pending = [asked({ minutes: 20 })];
    renderWithProviders(<RequestsCard username="kid" name="Kid" kinds={['time']} />);
    const r = await row();
    const picks = within(within(r).getByRole('group', { name: 'Quick picks' })).getAllByRole('button').map((b) => b.textContent);
    expect(picks).toEqual(['15', '20', '30', '45', '60']);
    fireEvent.click(within(r).getByRole('button', { name: '45' }));
    expect(within(r).getByRole('button', { name: '45' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(within(r).getByRole('button', { name: 'Approve 45 min' }));
    await waitFor(() => expect(decided).toEqual([{ id: '22', body: { grant: true, minutes: 45 } }]));
  });

  it('will not approve an amount out of range', async () => {
    renderWithProviders(<RequestsCard username="kid" name="Kid" kinds={['time']} />);
    const r = await row();
    for (const bad of ['3', '241', '', '12.5']) {
      fireEvent.change(within(r).getByLabelText('Give'), { target: { value: bad } });
      expect(within(r).getByRole('button', { name: 'Approve' })).toBeDisabled();
      expect(r).toHaveTextContent('Enter 5 to 240 minutes.');
    }
    expect(decided).toEqual([]);
  });

  it('declines without minutes', async () => {
    renderWithProviders(<RequestsCard username="kid" name="Kid" kinds={['time']} />);
    fireEvent.click(within(await row()).getByRole('button', { name: 'Not today' }));
    await waitFor(() => expect(decided).toEqual([{ id: '22', body: { grant: false } }]));
  });
});

describe('grantMinutes', () => {
  it('takes whole minutes from 5 to 240 only', () => {
    expect(grantMinutes('5')).toBe(5);
    expect(grantMinutes(' 240 ')).toBe(240);
    for (const bad of ['4', '241', '', 'abc', '30.5', '-10']) expect(grantMinutes(bad)).toBeNull();
  });
});
