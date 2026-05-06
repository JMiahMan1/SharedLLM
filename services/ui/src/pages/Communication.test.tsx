import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import Communication from './Communication';
import { renderWithProviders } from '../test/render';

describe('Communication page', () => {
  it('renders live execution-backed sections', async () => {
    renderWithProviders(<Communication />);

    expect(await screen.findByText('Active Timers')).toBeInTheDocument();
    expect(screen.getByText('Announcements')).toBeInTheDocument();
    expect(screen.getByText('Calendar')).toBeInTheDocument();
    expect(screen.getByText('Notes')).toBeInTheDocument();
    expect(await screen.findByText('Kitchen Timer')).toBeInTheDocument();
  });

  it('creates and deletes a timer', async () => {
    renderWithProviders(<Communication />);

    fireEvent.change(await screen.findByPlaceholderText('Timer title'), {
      target: { value: 'Laundry Timer' },
    });
    fireEvent.change(screen.getByPlaceholderText('10m'), {
      target: { value: '15m' },
    });
    fireEvent.click(screen.getByText('Add Timer'));

    expect(await screen.findByText('Laundry Timer')).toBeInTheDocument();

    const deleteButtons = screen.getAllByLabelText(/Delete/i);
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => expect(screen.queryByText('Kitchen Timer')).not.toBeInTheDocument());
  });

  it('sends announcements and executes note actions', async () => {
    renderWithProviders(<Communication />);

    fireEvent.change(await screen.findByRole('combobox'), {
      target: { value: 'media_player.office_speaker' },
    });
    fireEvent.click(screen.getByText('Send Announcement'));

    fireEvent.change(screen.getByPlaceholderText('Note title'), {
      target: { value: 'Shared Checklist' },
    });
    fireEvent.change(screen.getByPlaceholderText('Note content'), {
      target: { value: 'Pick up groceries' },
    });
    fireEvent.click(screen.getByText('Read'));
    expect(await screen.findByText(/Pick up groceries/)).toBeInTheDocument();
  });
});
