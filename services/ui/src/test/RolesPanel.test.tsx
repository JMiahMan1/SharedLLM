import { describe, it, expect } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { RolesPanel } from '../components/admin/RolesPanel';
import type { UserProfile } from '../types/api';
import { renderWithProviders } from './render';
import { server } from './setup';

const users = [
  { id: 1, username: 'default', is_admin: true, role: 'admin', is_me: true },
  { id: 2, username: 'michele', is_admin: false, role: 'user', is_me: false, role_ids: [] },
] as UserProfile[];

describe('RolesPanel', () => {
  it('lists roles and gives one to a person', async () => {
    const saved: unknown[] = [];
    const memberships: unknown[] = [];
    server.use(
      http.patch('/api/users/roles/:id', async ({ request }) => {
        const body = await request.json() as Record<string, unknown>;
        saved.push(body);
        return HttpResponse.json({ id: 1, builtin: true, members: [], ...body });
      }),
      http.put('/api/users/:username/roles', async ({ params, request }) => {
        memberships.push({ username: params.username, ...(await request.json() as object) });
        return HttpResponse.json({ ...users[1], role_ids: [1] });
      }),
    );
    renderWithProviders(<RolesPanel users={users} />);

    fireEvent.click(await screen.findByText('Home manager'));
    // Admins hold everything already, so only michele is offered.
    expect(screen.queryByLabelText('default')).toBeNull();
    // A built-in role cannot be deleted.
    expect(screen.queryByText('Delete')).toBeNull();
    fireEvent.click(screen.getByLabelText('michele'));
    fireEvent.click(screen.getByText('Save role'));

    await waitFor(() => expect(memberships).toEqual([{ username: 'michele', role_ids: [1] }]));
    expect(saved).toEqual([{ name: 'Home manager', description: 'Runs the house.', permissions: ['devices.use_all', 'devices.assign'] }]);
  });
});
