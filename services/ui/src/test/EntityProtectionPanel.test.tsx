import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { EntityProtectionPanel } from '../components/admin/EntityProtectionPanel';
import type { EntityProtection, UserProfile } from '../types/api';
import { renderWithProviders } from './render';
import { server } from './setup';

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

const user = (over: Partial<UserProfile> = {}): UserProfile => ({
  id: 1,
  username: 'alice',
  full_name: '',
  role: 'user',
  is_admin: false,
  ...over,
});

const users: UserProfile[] = [
  user({ username: 'default', role: 'admin', is_admin: true }),
  user({ username: 'alice' }),
  user({ id: 2, username: 'bob' }),
];

let stored: EntityProtection[] = [];
let puts: { url: string; body: unknown }[] = [];

const seed = () => {
  server.use(
    http.get('/api/entity-protection', () => HttpResponse.json(stored)),
    http.get('/api/entities', () =>
      HttpResponse.json({
        entities: [
          { entity_id: 'climate.hallway', friendly_name: 'Hallway', state: 'heat', domain: 'climate' },
          { entity_id: 'light.kitchen', friendly_name: 'Kitchen', state: 'on', domain: 'light' },
        ],
      })
    ),
    http.put('/api/entity-protection/:entityId', async ({ params, request }) => {
      const entityId = String(params.entityId);
      const body = (await request.json()) as {
        protected: boolean;
        permitted_usernames: string[];
        note: string | null;
      };
      puts.push({ url: entityId, body });
      if (!body.protected) {
        stored = stored.filter((p) => p.entity_id !== entityId);
        return HttpResponse.json({ entity_id: entityId, permitted_usernames: [], granted_by: 'default' });
      }
      const row: EntityProtection = {
        entity_id: entityId,
        permitted_usernames: body.permitted_usernames,
        granted_by: 'default',
        granted_at: '2026-01-01T00:00:00+00:00',
        note: body.note,
      };
      stored = [...stored.filter((p) => p.entity_id !== entityId), row];
      return HttpResponse.json(row);
    })
  );
};

const pickEntity = async (entityId: string) => {
  const box = await screen.findByPlaceholderText('Search Home Assistant entities...');
  fireEvent.focus(box);
  fireEvent.click(await screen.findByText('Hallway'));
  expect(screen.getByDisplayValue('Hallway')).toBeInTheDocument();
  void entityId;
};

const render = () => renderWithProviders(<EntityProtectionPanel users={users} />);

/** Exact name: a loose /protect/i also matches "Release protection for ...". */
const protectButton = () => screen.getByRole('button', { name: 'Protect' });

describe('EntityProtectionPanel', () => {
  beforeEach(() => {
    stored = [];
    puts = [];
    seed();
  });

  it('says plainly that nothing is protected to begin with', async () => {
    render();
    expect(await screen.findByText('No entities are protected.')).toBeInTheDocument();
  });

  it('locks an entity to admins only when no one is permitted', async () => {
    render();
    await pickEntity('climate.hallway');
    fireEvent.click(protectButton());

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({
      url: 'climate.hallway',
      body: { protected: true, permitted_usernames: [], note: null },
    });
  });

  it('permits a named user, and does not offer the admin or default account', async () => {
    render();
    await pickEntity('climate.hallway');

    // Admins and the default user bypass every lock, so a checkbox for them
    // would be a no-op that reads as though it did something.
    expect(screen.getByText(/Always allowed, lock or not:/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Permit @default')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Permit @alice')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Permit @bob'));
    fireEvent.click(protectButton());

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].body).toMatchObject({ permitted_usernames: ['bob'] });
  });

  it('carries the note through to the server', async () => {
    render();
    await pickEntity('climate.hallway');
    fireEvent.change(screen.getByLabelText('Protection note'), { target: { value: 'guest tablet' } });
    fireEvent.click(protectButton());

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].body).toMatchObject({ note: 'guest tablet' });
  });

  it('lists a locked entity with its permit list and who locked it', async () => {
    stored = [{
      entity_id: 'climate.hallway',
      permitted_usernames: ['bob'],
      granted_by: 'default',
      granted_at: '2026-01-01T00:00:00+00:00',
      note: 'upstairs',
    }];
    render();

    expect(await screen.findByText('climate.hallway')).toBeInTheDocument();
    expect(screen.getByText('Also permitted: @bob')).toBeInTheDocument();
    expect(screen.getByText(/@default/)).toBeInTheDocument();
  });

  it('loads the current permit list when an admin edits an existing lock', async () => {
    stored = [{
      entity_id: 'climate.hallway',
      permitted_usernames: ['bob'],
      granted_by: 'default',
      granted_at: '2026-01-01T00:00:00+00:00',
      note: null,
    }];
    render();

    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    expect(await screen.findByLabelText('Permit @bob')).toBeChecked();
    expect(screen.getByLabelText('Permit @alice')).not.toBeChecked();
    // Re-saving replaces the list rather than adding a second competing lock.
    expect(screen.getByRole('button', { name: 'Update Lock' })).toBeInTheDocument();
  });

  it('releases a lock, which tells the server to drop the permit list too', async () => {
    stored = [{
      entity_id: 'climate.hallway',
      permitted_usernames: ['bob'],
      granted_by: 'default',
      granted_at: '2026-01-01T00:00:00+00:00',
      note: null,
    }];
    render();

    fireEvent.click(await screen.findByRole('button', { name: 'Release protection for climate.hallway' }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({
      url: 'climate.hallway',
      body: { protected: false, permitted_usernames: [], note: null },
    });
    await waitFor(() => expect(screen.queryByText('climate.hallway')).not.toBeInTheDocument());
  });

  it('refuses to submit an unknown entity rather than locking nothing', async () => {
    render();
    // No entity chosen: the Protect button is disabled, so no request is made.
    expect(await screen.findByRole('button', { name: 'Protect' })).toBeDisabled();
    expect(puts).toHaveLength(0);
  });

  it('surfaces a server refusal rather than reporting success', async () => {
    server.use(
      http.put('/api/entity-protection/:entityId', () =>
        HttpResponse.json({ detail: 'Unknown username(s) in the permit list: alise.' }, { status: 400 })
      )
    );
    render();
    await pickEntity('climate.hallway');
    fireEvent.click(screen.getByLabelText('Permit @alice'));
    fireEvent.click(protectButton());

    // A typo'd grant must be visible, not silently stored as a permit to nobody.
    const toast = (await import('react-hot-toast')).default;
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining('Unknown username')
      )
    );
    expect(stored).toHaveLength(0);
  });

  it('gives every control a 44px touch target, because this ships to phones', async () => {
    render();
    await pickEntity('climate.hallway');
    const controls = [
      protectButton(),
      screen.getByLabelText('Permit @alice').closest('label') as HTMLElement,
    ];
    for (const control of controls) {
      expect(control.className).toMatch(/pointer-coarse:(min-h-11|h-11)/);
    }
  });
});
