import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import AreasPanel from './AreasPanel';
import LabelChips from './LabelChips';
import PersonsPanel from './PersonsPanel';
import ZonesPanel from './ZonesPanel';
import * as setup from '../../lib/homeSetup';

vi.mock('../../lib/homeSetup', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return {
    ...real,
    getAreas: vi.fn(), getFloors: vi.fn(), createArea: vi.fn(), updateArea: vi.fn(), deleteArea: vi.fn(),
    createFloor: vi.fn(), updateFloor: vi.fn(), deleteFloor: vi.fn(),
    getZones: vi.fn(), createZone: vi.fn(), updateZone: vi.fn(), deleteZone: vi.fn(), updateHome: vi.fn(),
    getPersons: vi.fn(), createPerson: vi.fn(), updatePerson: vi.fn(), deletePerson: vi.fn(),
  };
});
vi.mock('../../services/api', async (importOriginal) => {
  const real = (await importOriginal()) as { api: Record<string, unknown> };
  return { ...real, api: { ...real.api, getFamily: vi.fn(async () => [{ username: 'sky' }]) } };
});
vi.mock('../ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});

const m = vi.mocked(setup);

function wrap(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<MemoryRouter><QueryClientProvider client={client}>{ui}</QueryClientProvider></MemoryRouter>);
}

beforeEach(() => vi.clearAllMocks());

describe('AreasPanel', () => {
  beforeEach(() => {
    m.getFloors.mockResolvedValue([{ floor_id: 'up', name: 'Upstairs', level: 1, icon: null, aliases: [] }]);
    m.getAreas.mockResolvedValue([
      { area_id: 'den', name: 'Den', floor_id: null, icon: null, aliases: [], labels: [] },
      { area_id: 'loft', name: 'Loft', floor_id: 'up', icon: null, aliases: [], labels: [] },
    ]);
  });

  it('groups rooms by floor, moves one to a floor and adds a room on a floor', async () => {
    m.updateArea.mockResolvedValue({ area_id: 'den', name: 'Den', floor_id: 'up', icon: null, aliases: [], labels: [] });
    m.createArea.mockResolvedValue({ area_id: 'study', name: 'Study', floor_id: 'up', icon: null, aliases: [], labels: [] });
    wrap(<AreasPanel />);
    const groups = await screen.findAllByTestId('area-group');
    expect(groups.map((g) => within(g).getAllByTestId('area').map((a) => a.querySelector('p')?.textContent)))
      .toEqual([['Loft'], ['Den']]);
    fireEvent.change(screen.getByLabelText('Floor of Den'), { target: { value: 'up' } });
    await waitFor(() => expect(m.updateArea).toHaveBeenCalledWith('den', { floor_id: 'up' }));
    fireEvent.change(screen.getByLabelText('New room name'), { target: { value: ' Study ' } });
    fireEvent.change(screen.getByLabelText('Floor of the new room'), { target: { value: 'up' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add room' }));
    await waitFor(() => expect(m.createArea).toHaveBeenCalledWith({ name: 'Study', floor_id: 'up' }));
  });

  it('adds a floor with its level and removes one after asking', async () => {
    m.createFloor.mockResolvedValue({ floor_id: 'down', name: 'Down', level: 0, icon: null, aliases: [] });
    m.deleteFloor.mockResolvedValue({ areas: ['loft'] });
    wrap(<AreasPanel />);
    await screen.findAllByTestId('floor');
    fireEvent.change(screen.getByLabelText('New floor name'), { target: { value: 'Down' } });
    fireEvent.change(screen.getByLabelText('New floor level'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add floor' }));
    await waitFor(() => expect(m.createFloor).toHaveBeenCalledWith({ name: 'Down', level: 0 }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove Upstairs' }));
    await waitFor(() => expect(m.deleteFloor).toHaveBeenCalledWith('up'));
  });
});

describe('LabelChips', () => {
  it('toggles a label and says when there are none', () => {
    const change = vi.fn();
    const labels = [{ label_id: 'a', name: 'Alpha', color: 'red', icon: null, description: null },
      { label_id: 'b', name: 'Beta', color: null, icon: null, description: null }];
    const { rerender } = render(<LabelChips labels={labels} selected={['a']} name="Lamp" onChange={change} />);
    expect(screen.getByRole('button', { name: 'Alpha' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Beta' }));
    expect(change).toHaveBeenCalledWith(['a', 'b']);
    fireEvent.click(screen.getByRole('button', { name: 'Alpha' }));
    expect(change).toHaveBeenLastCalledWith([]);
    rerender(<LabelChips labels={[]} selected={[]} name="Lamp" onChange={change} />);
    expect(screen.getByText(/No labels yet/)).toBeInTheDocument();
  });
});

const HOME: setup.Zone = { id: 'home', entity_id: 'zone.home', name: 'Home', latitude: 10, longitude: 20, radius: 100,
  passive: false, icon: 'mdi:home', source: 'home', editable: true, removable: false, owner_name: null,
  visibility: 'family' };
const SAVED: setup.Zone = { id: 'a1', entity_id: 'zone.pond', name: 'Pond', latitude: 1, longitude: 2, radius: 150,
  passive: false, icon: null, source: 'hub', editable: true, removable: true, owner_name: 'sky', visibility: 'family' };

describe('ZonesPanel', () => {
  it('moves Home but never offers to remove it', async () => {
    m.getZones.mockResolvedValue([HOME, SAVED]);
    m.updateHome.mockResolvedValue({ ...HOME, latitude: 11, longitude: 21, radius: 250 });
    wrap(<ZonesPanel />);
    const home = await screen.findByTestId('zone-home');
    expect(within(home).getByTestId('home-note')).toHaveTextContent('Home cannot be removed');
    expect(within(home).queryByRole('button', { name: 'Remove Home' })).toBeNull();
    fireEvent.click(within(home).getByRole('button', { name: 'Edit Home' }));
    const editor = within(home).getByTestId('home-editor');
    expect(within(editor).queryByRole('checkbox')).toBeNull(); // Home is never passive
    fireEvent.change(within(editor).getByLabelText('Latitude'), { target: { value: '11, 21' } });
    fireEvent.change(within(editor).getByLabelText('Radius (metres)'), { target: { value: '250' } });
    fireEvent.click(within(editor).getByRole('button', { name: 'Save Home' }));
    await waitFor(() => expect(m.updateHome).toHaveBeenCalledWith(
      { name: 'Home', latitude: 11, longitude: 21, radius: 250 }));
    expect(m.updateZone).not.toHaveBeenCalled();
  });

  it('shows who saved a place in Wander, and edits and removes it like any zone', async () => {
    m.getZones.mockResolvedValue([HOME, SAVED]);
    m.deleteZone.mockResolvedValue({ entity_id: 'zone.pond' });
    wrap(<ZonesPanel />);
    const zone = await screen.findByTestId('zone');
    expect(zone).toHaveTextContent('Saved in Wander by sky');
    expect(within(zone).getByRole('link', { name: /Wander > Places/ })).toHaveAttribute('href', '/wander#wander-places');
    expect(within(zone).getByRole('button', { name: 'Edit Pond' })).toBeInTheDocument();
    fireEvent.click(within(zone).getByRole('button', { name: 'Remove Pond' }));
    await waitFor(() => expect(m.deleteZone).toHaveBeenCalledWith('a1'));
    expect(screen.queryByRole('button', { name: /Saved places/ })).toBeNull(); // no mirror to follow any more
  });

  it('refuses a zone with no radius before sending it', async () => {
    m.getZones.mockResolvedValue([HOME]);
    m.createZone.mockResolvedValue({ id: 'z', entity_id: 'zone.park', name: 'Park', latitude: 1, longitude: 2, radius: 50,
      passive: false, icon: null, source: 'hub', editable: true });
    wrap(<ZonesPanel />);
    await screen.findByTestId('zone-home');
    fireEvent.click(screen.getByRole('button', { name: 'New zone' }));
    const editor = screen.getByTestId('zone-editor');
    fireEvent.change(within(editor).getByLabelText('Name'), { target: { value: 'Park' } });
    fireEvent.change(within(editor).getByLabelText('Latitude'), { target: { value: '1, 2' } });
    expect(within(editor).getByLabelText('Longitude')).toHaveValue('2');
    fireEvent.change(within(editor).getByLabelText('Radius (metres)'), { target: { value: '' } });
    fireEvent.click(within(editor).getByRole('button', { name: 'Add zone' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The radius is 1 to 100000 metres.');
    expect(m.createZone).not.toHaveBeenCalled();
    fireEvent.change(within(editor).getByLabelText('Radius (metres)'), { target: { value: '50' } });
    fireEvent.click(within(editor).getByRole('button', { name: 'Add zone' }));
    await waitFor(() => expect(m.createZone).toHaveBeenCalledWith(
      { name: 'Park', latitude: 1, longitude: 2, radius: 50, passive: false }));
  });
});

describe('PersonsPanel', () => {
  it('links a new person to a Jarvis user and a free tracker only', async () => {
    m.getPersons.mockResolvedValue({
      persons: [{ id: 'p1', entity_id: 'person.robin', name: 'Robin', user_id: null, jarvis_user: 'robin',
        device_trackers: ['device_tracker.one'], state: 'home' }],
      trackers: [{ entity_id: 'device_tracker.one', name: 'Phone one', state: 'home' },
        { entity_id: 'device_tracker.two', name: 'Tablet two', state: null }],
    });
    m.createPerson.mockResolvedValue({ id: 'p2', entity_id: 'person.sky', name: 'Sky', user_id: null, jarvis_user: 'sky',
      device_trackers: ['device_tracker.two'], state: null });
    wrap(<PersonsPanel />);
    expect(await screen.findByTestId('person')).toHaveTextContent('Phone one');
    fireEvent.click(screen.getByRole('button', { name: 'New person' }));
    const editor = screen.getByTestId('person-editor');
    fireEvent.change(within(editor).getByLabelText('Name'), { target: { value: 'Sky' } });
    fireEvent.change(within(editor).getByLabelText('Jarvis user'), { target: { value: ' SKY ' } });
    expect(within(editor).getByRole('checkbox', { name: 'Phone one' })).toBeDisabled();
    fireEvent.click(within(editor).getByRole('checkbox', { name: 'Tablet two' }));
    fireEvent.click(within(editor).getByRole('button', { name: 'Add person' }));
    await waitFor(() => expect(m.createPerson).toHaveBeenCalledWith(
      { name: 'Sky', jarvis_user: 'sky', device_trackers: ['device_tracker.two'] }));
  });
});
