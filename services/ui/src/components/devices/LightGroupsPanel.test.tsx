import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import LightGroupsPanel from './LightGroupsPanel';
import * as lg from '../../lib/lightGroups';
import {
  bodyOf, choicesByRoom, draftOf, draftProblem, memberSummary, toggleLight,
  type LightGroup, type LightGroups,
} from '../../lib/lightGroups';

vi.mock('../../lib/lightGroups', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  getLightGroups: vi.fn(),
  createLightGroup: vi.fn(),
  updateLightGroup: vi.fn(),
  deleteLightGroup: vi.fn(),
}));
vi.mock('../ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));

const light = (n: number, area = 'Pantry', extra = {}) => ({
  entity_id: `light.pantry_${n}`, name: `Pantry Light ${n}`, area, hidden: false, available: true, ...extra,
});

const GROUP: LightGroup = {
  entry_id: 'g1', entity_id: 'light.pantry_lights', name: 'Pantry lights', area_id: 'pantry', area: 'Pantry',
  state: 'on', missing: 1,
  members: [
    { entity_id: 'light.pantry_1', name: 'Pantry Light 1', state: 'on', missing: false, hidden: false, area: 'Pantry' },
    { entity_id: 'light.pantry_2', name: 'Pantry Light 2', state: 'off', missing: false, hidden: true, area: 'Pantry' },
    { entity_id: 'light.old_bulb', name: 'old bulb', state: null, missing: true, hidden: false, area: null },
  ],
};

const DATA: LightGroups = {
  groups: [GROUP],
  lights: [light(1), light(2, 'Pantry', { hidden: true }), light(3), { ...light(4, 'Den'), name: 'Den Lamp' },
    { ...light(5, ''), area: null, name: 'Porch Lamp', available: false }],
  areas: [{ area_id: 'den', name: 'Den' }, { area_id: 'pantry', name: 'Pantry' }],
};

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><LightGroupsPanel /></QueryClientProvider>);
}

describe('light group helpers', () => {
  it('needs a name and two lights', () => {
    expect(draftProblem({ name: ' ', area_id: '', entities: ['a', 'b'] })).toMatch(/name/);
    expect(draftProblem({ name: 'x', area_id: '', entities: ['a'] })).toMatch(/two/);
    expect(draftProblem({ name: 'x', area_id: '', entities: ['a', 'b'] })).toBeNull();
  });

  it('sends a tidy body and reads a group back into a draft', () => {
    expect(bodyOf({ name: '  Pantry   lights ', area_id: '', entities: ['a', 'b'] }))
      .toEqual({ name: 'Pantry lights', area_id: null, entities: ['a', 'b'] });
    expect(draftOf(GROUP)).toEqual({ name: 'Pantry lights', area_id: 'pantry',
      entities: ['light.pantry_1', 'light.pantry_2', 'light.old_bulb'] });
    expect(toggleLight(draftOf(GROUP), 'light.pantry_1').entities).not.toContain('light.pantry_1');
    expect(toggleLight(draftOf(), 'x').entities).toEqual(['x']);
  });

  it('says how many lights, how many are gone, and how many are on', () => {
    expect(memberSummary(GROUP)).toBe('3 lights · 1 is gone · 1 on');
    expect(memberSummary({ ...GROUP, missing: 0, members: GROUP.members.slice(0, 1) })).toBe('1 light');
  });

  it('lists the lights by room, search included, a gone member first and no room last', () => {
    const rooms = choicesByRoom(DATA, draftOf(GROUP), '', GROUP.members);
    expect(rooms.map((r) => r.room)).toEqual(['No longer in the hub', 'Den', 'Pantry', 'No room']);
    expect(rooms[0].lights.map((l) => l.entity_id)).toEqual(['light.old_bulb']);
    expect(choicesByRoom(DATA, draftOf(), 'den').map((r) => r.room)).toEqual(['Den']);
    expect(choicesByRoom(DATA, draftOf(), 'light 3')[0].lights.map((l) => l.name)).toEqual(['Pantry Light 3']);
  });
});

describe('LightGroupsPanel', () => {
  beforeEach(() => {
    vi.mocked(lg.getLightGroups).mockResolvedValue(DATA);
    vi.mocked(lg.createLightGroup).mockResolvedValue({ ...GROUP, entry_id: 'g2', name: 'Den and pantry' });
    vi.mocked(lg.updateLightGroup).mockResolvedValue(GROUP);
    vi.mocked(lg.deleteLightGroup).mockResolvedValue();
  });

  it('shows each group with its room, its lights and what is hidden or gone', async () => {
    renderPanel();
    const row = await screen.findByTestId('light-group');
    expect(within(row).getByRole('heading', { name: 'Pantry lights' })).toBeTruthy();
    expect(row.textContent).toContain('Pantry · 3 lights · 1 is gone · 1 on');
    const members = within(row).getAllByTestId('light-group-member').map((m) => m.textContent);
    expect(members).toEqual(['Pantry Light 1', 'Pantry Light 2hidden copy', 'old bulbgone']);
  });

  it('makes a group from lights picked by room', async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /New group/ }));
    const editor = screen.getByTestId('light-group-editor');
    fireEvent.change(within(editor).getByLabelText('Name'), { target: { value: 'Den and pantry' } });
    fireEvent.change(within(editor).getByLabelText('Room'), { target: { value: 'den' } });
    // Too few lights: it says so instead of sending.
    fireEvent.click(within(editor).getByRole('button', { name: 'Make group' }));
    expect(within(editor).getByRole('alert').textContent).toMatch(/two lights/);
    expect(lg.createLightGroup).not.toHaveBeenCalled();
    fireEvent.click(within(editor).getByRole('button', { name: 'Choose every light in Pantry' }));
    fireEvent.click(within(editor).getByRole('checkbox', { name: 'Den Lamp' }));
    expect(within(editor).getByTestId('light-group-count').textContent).toBe('4 lights chosen');
    fireEvent.click(within(editor).getByRole('button', { name: 'Make group' }));
    await waitFor(() => expect(lg.createLightGroup).toHaveBeenCalledWith({
      name: 'Den and pantry', area_id: 'den',
      entities: ['light.pantry_1', 'light.pantry_2', 'light.pantry_3', 'light.pantry_4'],
    }));
    await waitFor(() => expect(screen.queryByTestId('light-group-editor')).toBeNull());
  });

  it('changes a group, taking out a light that is gone', async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Change Pantry lights' }));
    const editor = screen.getByTestId('light-group-editor');
    expect(within(editor).getByText('No longer in the hub')).toBeTruthy();
    fireEvent.click(within(editor).getByRole('checkbox', { name: /old bulb/ }));
    fireEvent.click(within(editor).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(lg.updateLightGroup).toHaveBeenCalledWith('g1', {
      name: 'Pantry lights', area_id: 'pantry', entities: ['light.pantry_1', 'light.pantry_2'],
    }));
  });

  it('removes a group after asking', async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Pantry lights' }));
    await waitFor(() => expect(lg.deleteLightGroup).toHaveBeenCalledWith('g1'));
  });

  it('says when there are none', async () => {
    vi.mocked(lg.getLightGroups).mockResolvedValue({ ...DATA, groups: [] });
    renderPanel();
    expect((await screen.findByTestId('light-groups-empty')).textContent).toMatch(/No light groups yet/);
  });
});
