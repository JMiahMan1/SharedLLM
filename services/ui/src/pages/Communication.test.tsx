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
    expect(screen.getAllByText('Notes').length).toBeGreaterThan(0);
    expect(await screen.findByText('Kitchen Timer')).toBeInTheDocument();
    expect(await screen.findByText('Chat moved to Family')).toBeInTheDocument();
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

  it('points chat at the Family page instead of hosting a second chat UI', async () => {
    renderWithProviders(<Communication />);

    // Chat has one home now (Family). Communication must not grow its own copy
    // again — the Talk composer and conversation controls are gone from here.
    expect(await screen.findByText('Chat moved to Family')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /open family/i })).toHaveAttribute('href', '/family');
    expect(screen.queryByPlaceholderText('Send a live Nextcloud Talk message')).not.toBeInTheDocument();
  });
});
