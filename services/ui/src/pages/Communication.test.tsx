import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import Communication from './Communication';
import { renderWithProviders } from '../test/render';

describe('Communication page', () => {
  it('renders live execution-backed sections', async () => {
    renderWithProviders(<Communication />);

    expect(await screen.findByText('Active Timers')).toBeInTheDocument();
    expect(screen.getByText('Announcements')).toBeInTheDocument();
    expect(screen.getByText('Nextcloud Talk')).toBeInTheDocument();
    expect(screen.getByText('Calendar')).toBeInTheDocument();
    expect(screen.getByText('Notes')).toBeInTheDocument();
    expect(await screen.findByText('Kitchen Timer')).toBeInTheDocument();
    expect(await screen.findByText('Family')).toBeInTheDocument();
  });

  it('creates and deletes a timer', async () => {
    renderWithProviders(<Communication />);

    fireEvent.change(await screen.findByPlaceholderText('Timer name'), {
      target: { value: 'Laundry Timer' },
    });
    fireEvent.change(screen.getByPlaceholderText('Duration or time expression'), {
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
    fireEvent.change(screen.getByPlaceholderText('Enter the announcement message'), {
      target: { value: 'Dinner is ready.' },
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

  it('opens a talk conversation and sends a chat message', async () => {
    renderWithProviders(<Communication />);

    fireEvent.change(await screen.findByPlaceholderText('Nextcloud username to open'), {
      target: { value: 'jeremiah' },
    });
    fireEvent.click(screen.getByText('Open Conversation'));

    expect(await screen.findByText('DM jeremiah')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('Send a live Nextcloud Talk message'), {
      target: { value: 'Testing live talk.' },
    });
    fireEvent.click(screen.getByText('Send Message'));

    expect(await screen.findByText('Testing live talk.')).toBeInTheDocument();
  });

  it('records and sends a talk voice message', async () => {
    renderWithProviders(<Communication />);

    expect(await screen.findByText('Family')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Record Voice'));
    fireEvent.click(await screen.findByText('Stop Recording'));

    await screen.findByText(/Recorded clip ready/);

    fireEvent.change(screen.getByPlaceholderText('Optional caption for voice message'), {
      target: { value: 'Voice update' },
    });
    fireEvent.click(screen.getByText('Send Voice'));

    expect((await screen.findAllByText('Voice update')).length).toBeGreaterThan(0);
  });
});
