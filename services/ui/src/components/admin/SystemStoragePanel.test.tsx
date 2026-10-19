import { fireEvent, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import SystemStoragePanel from './SystemStoragePanel';
import { renderWithProviders } from '../../test/render';
import { server } from '../../test/setup';

describe('SystemStoragePanel', () => {
  it('lists each user and saves a new quota in GB', async () => {
    let saved: unknown = null;
    server.use(
      http.get('/api/storage/usage/system', () => HttpResponse.json({
        users: [
          {
            user: 'jeremiah',
            used: 1.5 * 1024 ** 3,
            quota: 5 * 1024 ** 3,
            free: 20 * 1024 ** 3,
            count: 4,
            by_kind: { image: 1.5 * 1024 ** 3 },
            warn: false,
          },
        ],
        total: 1.5 * 1024 ** 3,
        free: 20 * 1024 ** 3,
        quota: 5 * 1024 ** 3,
        used: 1.5 * 1024 ** 3,
      })),
      http.put('/api/storage/quota/jeremiah', async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json({
          user: 'jeremiah',
          used: 1.5 * 1024 ** 3,
          quota: 10 * 1024 ** 3,
          free: 20 * 1024 ** 3,
          count: 4,
          by_kind: {},
          warn: false,
        });
      }),
    );
    renderWithProviders(<SystemStoragePanel />);

    expect(await screen.findByText('jeremiah')).toBeInTheDocument();
    expect(screen.getByText(/1.50 GB of 5.00 GB/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Quota in GB for jeremiah'), { target: { value: '10' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save quota for jeremiah' }));

    await waitFor(() => expect(saved).toEqual({ quota_bytes: 10 * 1024 ** 3 }));
  });
});
