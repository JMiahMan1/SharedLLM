import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import PlacesPanel, { type PlaceSetting } from './PlacesPanel';
import { api } from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, api: { ...(actual.api as Record<string, unknown>), setPlaceSettings: vi.fn() } };
});

const setPlaceSettings = vi.mocked(api.setPlaceSettings);
const members = [
  { username: 'michele', name: 'Michele' },
  { username: 'leo', name: 'Leo' },
];
const school: PlaceSetting = { id: 'zone.school', name: 'School', important: false, notify: [] };

describe('PlacesPanel', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setPlaceSettings.mockResolvedValue({ zone: 'zone.school', important: true, notify: [] });
  });

  it('marks a place important', async () => {
    const onSaved = vi.fn();
    render(<PlacesPanel places={[school]} members={members} canEdit onSaved={onSaved} />);
    await userEvent.click(screen.getByRole('switch', { name: /school is important/i }));
    await waitFor(() => expect(setPlaceSettings).toHaveBeenCalledWith('zone.school', { important: true, notify: [] }));
    expect(onSaved).toHaveBeenCalled();
  });

  it('chooses who hears about an important place', async () => {
    render(<PlacesPanel places={[{ ...school, important: true }]} members={members} canEdit onSaved={vi.fn()} />);
    const row = screen.getByTestId('place-zone.school');
    expect(row).toHaveTextContent(/alerts go to everyone/i);
    await userEvent.click(within(row).getByRole('button', { name: 'Michele' }));
    await waitFor(() =>
      expect(setPlaceSettings).toHaveBeenCalledWith('zone.school', { important: true, notify: ['michele'] }),
    );
  });

  it('turning a place off clears its list', async () => {
    render(<PlacesPanel places={[{ ...school, important: true, notify: ['leo'] }]} members={members} canEdit onSaved={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Leo' })).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(screen.getByRole('switch', { name: /school is important/i }));
    await waitFor(() => expect(setPlaceSettings).toHaveBeenCalledWith('zone.school', { important: false, notify: [] }));
  });

  it('shows the settings read only to someone who cannot change them', () => {
    render(
      <PlacesPanel
        places={[{ ...school, important: true, notify: ['leo'] }, { ...school, id: 'zone.park', name: 'Park' }]}
        members={members}
        canEdit={false}
        onSaved={vi.fn()}
      />,
    );
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Leo' })).not.toBeInTheDocument();
    expect(screen.getByTestId('place-zone.school')).toHaveTextContent(/important.*alerts go to leo\./i);
    expect(screen.getByTestId('place-zone.park')).not.toHaveTextContent(/important/i);
    expect(screen.getByTestId('places-panel')).toHaveTextContent(/an admin chooses the places/i);
  });

  it('shows nothing with no places', () => {
    const { container } = render(<PlacesPanel places={[]} members={members} canEdit onSaved={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
