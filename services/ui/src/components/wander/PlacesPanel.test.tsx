import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactElement } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';
import PlacesPanel, { type PlaceSetting } from './PlacesPanel';
import { api } from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      setPlaceSettings: vi.fn(),
      getSavedPlaces: vi.fn(),
      getVisitSuggestions: vi.fn(),
    },
  };
});

const setPlaceSettings = vi.mocked(api.setPlaceSettings);
const getSavedPlaces = vi.mocked(api.getSavedPlaces);
const getVisitSuggestions = vi.mocked(api.getVisitSuggestions);
const members = [
  { username: 'michele', name: 'Michele' },
  { username: 'leo', name: 'Leo' },
];
const school: PlaceSetting = { id: 'zone.school', name: 'School', important: false, notify: [] };

// The panel reads saved places and visit suggestions through react-query.
const renderPanel = (ui: ReactElement) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>,
  );

describe('PlacesPanel', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setPlaceSettings.mockResolvedValue({ zone: 'zone.school', important: true, notify: [] });
    getSavedPlaces.mockResolvedValue([]);
    getVisitSuggestions.mockResolvedValue([]);
  });

  it('marks a place important', async () => {
    const onSaved = vi.fn();
    renderPanel(<PlacesPanel places={[school]} members={members} canEdit onSaved={onSaved} currentUser="dad" isAdmin />);
    await userEvent.click(screen.getByRole('switch', { name: /school is important/i }));
    await waitFor(() => expect(setPlaceSettings).toHaveBeenCalledWith('zone.school', { important: true, notify: [] }));
    expect(onSaved).toHaveBeenCalled();
  });

  it('chooses who hears about an important place', async () => {
    renderPanel(<PlacesPanel places={[{ ...school, important: true }]} members={members} canEdit onSaved={vi.fn()} currentUser="dad" isAdmin />);
    const row = screen.getByTestId('place-zone.school');
    expect(row).toHaveTextContent(/alerts go to everyone/i);
    await userEvent.click(within(row).getByRole('button', { name: 'Michele' }));
    await waitFor(() =>
      expect(setPlaceSettings).toHaveBeenCalledWith('zone.school', { important: true, notify: ['michele'] }),
    );
  });

  it('turning a place off clears its list', async () => {
    renderPanel(<PlacesPanel places={[{ ...school, important: true, notify: ['leo'] }]} members={members} canEdit onSaved={vi.fn()} currentUser="dad" isAdmin />);
    expect(screen.getByRole('button', { name: 'Leo' })).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(screen.getByRole('switch', { name: /school is important/i }));
    await waitFor(() => expect(setPlaceSettings).toHaveBeenCalledWith('zone.school', { important: false, notify: [] }));
  });

  it('shows the settings read only to someone who cannot change them', () => {
    renderPanel(
      <PlacesPanel
        places={[{ ...school, important: true, notify: ['leo'] }, { ...school, id: 'zone.park', name: 'Park' }]}
        members={members}
        canEdit={false}
        onSaved={vi.fn()}
        currentUser="leo"
        isAdmin={false}
      />,
    );
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Leo' })).not.toBeInTheDocument();
    expect(screen.getByTestId('place-zone.school')).toHaveTextContent(/important.*alerts go to leo\./i);
    expect(screen.getByTestId('place-zone.park')).not.toHaveTextContent(/important/i);
    expect(screen.getByTestId('places-panel')).toHaveTextContent(/you can add your own places/i);
  });

  it('offers to save a place someone keeps visiting, ready to alert', async () => {
    getVisitSuggestions.mockResolvedValue([
      { lat: 33.4, lon: -111.9, count: 3, total_s: 5400, last_since: 1, last_ts: 2, place: 'Corner Cafe' },
    ]);
    renderPanel(<PlacesPanel places={[]} members={members} canEdit onSaved={vi.fn()} currentUser="dad" isAdmin />);
    await userEvent.click(await screen.findByRole('button', { name: /add as a place/i }));
    expect(screen.getByLabelText('Place name')).toHaveValue('Corner Cafe');
    // A spot you keep going back to starts as an important place, so the alert
    // picker is already open.
    expect(screen.getByTestId('place-form')).toHaveTextContent(/alerts go to everyone/i);
  });

  it('shows nothing with no places', () => {
    const { container } = renderPanel(<PlacesPanel places={[]} members={members} canEdit onSaved={vi.fn()} currentUser="dad" isAdmin />);
    expect(container).toBeEmptyDOMElement();
  });
});
