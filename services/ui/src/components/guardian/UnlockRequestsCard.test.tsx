import { beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import UnlockRequestsCard from './UnlockRequestsCard';
import { expiresIn, unlockLine } from '../../lib/unlockRequests';
import { renderWithProviders } from '../../test/render';
import { server } from '../../test/setup';
import type { UnlockRequest } from '../../types/api';

const NOW = Date.now();

function request(over: Partial<UnlockRequest> = {}): UnlockRequest {
  return {
    id: 7, username: 'kid', device: 'PC', lock_reason: 'bedtime', note: 'finishing homework', minutes_asked: 45,
    status: 'pending', minutes_granted: null, until: null, decided_by: null,
    created_at: new Date(NOW - 60_000).toISOString(), expires_at: new Date(NOW + 9 * 60_000).toISOString(), decided_at: null,
    ...over,
  };
}

let pending: UnlockRequest[] = [];
let decided: Array<{ id: string; body: unknown }> = [];
let refuse: { status: number; detail: string } | null = null;

beforeEach(() => {
  pending = [request(), request({ id: 8, username: 'other', device: 'phone' })];
  decided = [];
  refuse = null;
  server.use(
    http.get('*/api/guard/unlock-requests', () => HttpResponse.json({ requests: pending })),
    http.post('*/api/guard/unlock-requests/:id', async ({ params, request: req }) => {
      const body = await req.json();
      decided.push({ id: String(params.id), body });
      if (refuse) return HttpResponse.json({ detail: refuse.detail }, { status: refuse.status });
      pending = pending.filter((r) => String(r.id) !== String(params.id));
      return HttpResponse.json({ request: { ...request(), status: (body as { approve: boolean }).approve ? 'approved' : 'denied' } });
    }),
  );
});

describe('UnlockRequestsCard', () => {
  it('shows only this child\'s waiting request, in plain words', async () => {
    renderWithProviders(<UnlockRequestsCard username="kid" name="Kid" />);
    const row = await screen.findByTestId('unlock-request-7');
    expect(row).toHaveTextContent('Asks to unlock their PC for 45 minutes at bedtime: “finishing homework”');
    expect(row).toHaveTextContent(/expires in \d+ min/);
    expect(screen.queryByTestId('unlock-request-8')).not.toBeInTheDocument();
    expect(screen.getByText('1 waiting')).toBeInTheDocument();
  });

  it('approves for the minutes asked by default', async () => {
    renderWithProviders(<UnlockRequestsCard username="kid" name="Kid" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Approve for 45 min' }));
    await waitFor(() => expect(decided).toEqual([{ id: '7', body: { approve: true, minutes: 45 } }]));
    await waitFor(() => expect(screen.queryByTestId('unlock-requests')).not.toBeInTheDocument());
  });

  it('approves for another length the parent picks', async () => {
    renderWithProviders(<UnlockRequestsCard username="kid" name="Kid" />);
    fireEvent.click(await screen.findByRole('radio', { name: '2 h' }));
    expect(screen.getByRole('radio', { name: '2 h' })).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Approve for 2 h' }));
    await waitFor(() => expect(decided).toEqual([{ id: '7', body: { approve: true, minutes: 120 } }]));
  });

  it('denies', async () => {
    renderWithProviders(<UnlockRequestsCard username="kid" name="Kid" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Deny' }));
    await waitFor(() => expect(decided).toEqual([{ id: '7', body: { approve: false } }]));
  });

  it('says why when the hub refuses (an expired request)', async () => {
    refuse = { status: 409, detail: 'This request has expired. The device can ask again.' };
    renderWithProviders(<UnlockRequestsCard username="kid" name="Kid" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Approve for 45 min' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('This request has expired');
  });

  it('shows nothing while nothing is waiting, and an error when the list cannot load', async () => {
    pending = [];
    const { unmount } = renderWithProviders(<UnlockRequestsCard username="kid" name="Kid" />);
    await waitFor(() => expect(screen.queryByTestId('unlock-requests')).not.toBeInTheDocument());
    unmount();
    server.use(http.get('*/api/guard/unlock-requests', () => HttpResponse.json({ detail: 'boom' }, { status: 500 })));
    renderWithProviders(<UnlockRequestsCard username="kid" name="Kid" />);
    expect(await screen.findByTestId('unlock-requests-error')).toHaveTextContent("Kid's unlock requests could not be loaded.");
  });
});

describe('unlock words', () => {
  it('reads every lock reason and none', () => {
    expect(unlockLine(request({ lock_reason: 'limit', minutes_asked: 15, device: 'phone' }))).toBe("Asks to unlock their phone for 15 minutes after today's limit");
    expect(unlockLine(request({ lock_reason: '' }))).toBe('Asks to unlock their PC for 45 minutes');
  });

  it('counts down to the server\'s expiry', () => {
    expect(expiresIn(new Date(NOW + 4.5 * 60_000).toISOString(), NOW)).toBe('expires in 5 min');
    expect(expiresIn(new Date(NOW - 1000).toISOString(), NOW)).toBe('expiring now');
  });
});
