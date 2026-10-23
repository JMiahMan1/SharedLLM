import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import AnnouncementsPanel from './AnnouncementsPanel';
import { renderWithProviders } from '../../test/render';

describe('Announcements panel', () => {
  it('renders live execution-backed sections', async () => {
    renderWithProviders(<AnnouncementsPanel />);

    expect(await screen.findByText('Active Timers')).toBeInTheDocument();
    expect(screen.getByText('Announcements')).toBeInTheDocument();
    expect(await screen.findByText('Kitchen Timer')).toBeInTheDocument();
  });

  it('hosts no calendar, notes or chat copies, and points nowhere', async () => {
    renderWithProviders(<AnnouncementsPanel />);

    expect(await screen.findByText('Active Timers')).toBeInTheDocument();
    // The old notes editor (Save appended the note to itself), the calendar
    // panel that only printed "Loaded N events", and the cards that linked
    // out to /calendar and /notes are all gone — timers and announcements
    // are the whole panel.
    expect(screen.queryByPlaceholderText('Note title')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Calendar selection')).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('Send a live Nextcloud Talk message')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /open calendar/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /open notes/i })).not.toBeInTheDocument();
  });

  it('creates and deletes a timer', async () => {
    renderWithProviders(<AnnouncementsPanel />);

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

  it('supports configuring household alarms, emoji sounds, multi-device targets, and ESPHome screen flash', async () => {
    renderWithProviders(<AnnouncementsPanel />);

    fireEvent.click(await screen.findByRole('button', { name: /configure alarm, sound, emoji and devices/i }));

    // Select Alarm mode and Household scope
    fireEvent.click(screen.getByRole('button', { name: /^alarm$/i }));
    fireEvent.click(screen.getByRole('button', { name: /personal \(my devices\)/i }));
    expect(screen.getByRole('button', { name: /household \(family\)/i })).toBeInTheDocument();

    // Select Egg Timer emoji and Classic Digital Alarm sound
    fireEvent.click(screen.getByRole('button', { name: /🥚 egg timer/i }));
    fireEvent.click(screen.getByRole('button', { name: /classic digital alarm/i }));

    // Select Mobile App and ESPHome Kitchen Display (Screen Only)
    fireEvent.click(await screen.findByRole('button', { name: /mobile app \(phone alarm\)/i }));
    fireEvent.click(screen.getByRole('button', { name: /kitchen display \(esphome\)/i }));

    // Enable Flash Screen and set custom ESPHome script
    fireEvent.click(screen.getByRole('button', { name: /auto flash speakerless screens/i }));
    fireEvent.change(screen.getByLabelText('ESPHome script'), {
      target: { value: 'flash_screen' },
    });

    fireEvent.change(screen.getByPlaceholderText('Timer name'), {
      target: { value: 'Family Dinner Alarm' },
    });
    fireEvent.change(screen.getByLabelText('Duration or time'), {
      target: { value: '6:00pm' },
    });
    fireEvent.click(screen.getByText('Add Timer'));

    expect(await screen.findByText('Family Dinner Alarm')).toBeInTheDocument();
    expect(screen.getByText('Household')).toBeInTheDocument();
  });
});
