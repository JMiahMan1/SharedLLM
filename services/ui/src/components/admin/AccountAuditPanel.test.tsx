import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import AccountAuditPanel from './AccountAuditPanel';
import { renderWithProviders } from '../../test/render';
import { server } from '../../test/setup';

describe('AccountAuditPanel', () => {
  it('says who changed which login, without any values', async () => {
    server.use(
      http.get('/api/admin/audit', () =>
        HttpResponse.json({
          events: [
            {
              id: 1, at: '2026-10-08T15:00:00Z', actor: 'jeremiah', actor_kind: 'user', action: 'user.update', target: 'michele',
              changes: [{ field: 'nextcloud_pass', change: 'cleared', secret: true }, { field: 'is_admin', change: 'changed', to: false }],
              source: 'PATCH /api/users/michele', client: '192.168.2.30',
            },
          ],
        }),
      ),
    );
    renderWithProviders(<AccountAuditPanel users={['jeremiah', 'michele']} />);
    expect(await screen.findByText('jeremiah edited michele')).toBeInTheDocument();
    expect(screen.getByText(/Nextcloud password cleared · admin → off/)).toBeInTheDocument();
    expect(screen.getByText(/PATCH \/api\/users\/michele · 192\.168\.2\.30/)).toBeInTheDocument();
  });
});
