import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import Communication from './Communication';
import { renderWithProviders } from '../test/render';

describe('Communication page', () => {
  it('renders live execution-backed sections', async () => {
    renderWithProviders(<Communication />);

    expect(await screen.findByText('Active Timers')).toBeInTheDocument();
    expect(screen.getByText('Announcements')).toBeInTheDocument();
    expect(await screen.findByText('Kitchen Timer')).toBeInTheDocument();
  });

  it('links to the one calendar, notes and chat instead of hosting second copies', async () => {
    renderWithProviders(<Communication />);

    expect(await screen.findByRole('link', { name: /open calendar/i })).toHaveAttribute('href', '/calendar');
    expect(screen.getByRole('link', { name: /open notes/i })).toHaveAttribute('href', '/notes');
    // The old notes editor (Save appended the note to itself) and the calendar
    // panel that only printed "Loaded N events" are gone.
    expect(screen.queryByPlaceholderText('Note title')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Calendar selection')).not.toBeInTheDocument();
  });

  it('creates and deletes a timer', async () => {
    renderWithProviders(<Communication />);

    fireEvent.change(await screen.findByPlaceholderText('Timer name'), {
      target: { value: 'Laundry Timer' },
    });
    fireEvent.change(screen.getByLabelText('Duration or time'), {
      target: { value: '15m' },
    });
    fireEvent.click(screen.getByText('Add Timer'));

    expect(await screen.findByText('Laundry Timer')).toBeInTheDocument();

    const deleteButtons = screen.getAllByLabelText(/Delete/i);
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => expect(screen.queryByText('Kitchen Timer')).not.toBeInTheDocument());
  });

  it('points chat at the Family page instead of hosting a second chat UI', async () => {
    renderWithProviders(<Communication />);

    // Chat has one home now (Family). Communication must not grow its own copy
    // again — the Talk composer and conversation controls are gone from here.
    expect(await screen.findByRole('link', { name: /open family/i })).toHaveAttribute('href', '/family');
    expect(screen.queryByPlaceholderText('Send a live Nextcloud Talk message')).not.toBeInTheDocument();
  });
});
