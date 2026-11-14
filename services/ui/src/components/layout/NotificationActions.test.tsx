import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import NotificationActions from './NotificationActions';
import { api } from '../../services/api';
import type { NoticeAction } from '../../types/api';

vi.mock('../../services/api', () => ({ api: { pushAction: vi.fn() } }));

const pushAction = vi.mocked(api.pushAction);

const HOME_QUESTION: NoticeAction[] = [
  { label: 'Lock it', type: 'callback', callback: 'home_notification_action', params: { action: 'CONFIRM_c1', tag: 'door' } },
  { label: 'Say why', type: 'reply', callback: 'home_notification_action', params: { action: 'REPLY' } },
  { label: 'Open clip', type: 'view', url: 'https://cams.example.test/clip/1' },
];

describe('NotificationActions', () => {
  beforeEach(() => {
    pushAction.mockReset();
  });

  it('answers a callback button as the person and says it went through', async () => {
    pushAction.mockResolvedValue({ status: 'SUCCESS' });
    render(<NotificationActions actions={HOME_QUESTION} onOpen={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Lock it' }));
    await waitFor(() => expect(screen.getByTestId('notification-action-done')).toHaveTextContent('Sent: Lock it'));
    expect(pushAction).toHaveBeenCalledWith('home_notification_action', { action: 'CONFIRM_c1', tag: 'door' }, undefined);
  });

  it('asks for the text of a reply button before sending it', async () => {
    pushAction.mockResolvedValue({ status: 'SUCCESS' });
    render(<NotificationActions actions={HOME_QUESTION} onOpen={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Say why' }));
    const send = screen.getByRole('button', { name: 'Send' });
    expect(send).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Say why'), { target: { value: 'Back soon' } });
    fireEvent.click(send);
    await waitFor(() => expect(pushAction).toHaveBeenCalledWith('home_notification_action', { action: 'REPLY' }, 'Back soon'));
  });

  it('shows why an answer failed and keeps the buttons', async () => {
    pushAction.mockRejectedValue({ response: { data: { detail: 'The device hub did not answer' } } });
    render(<NotificationActions actions={HOME_QUESTION} onOpen={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Lock it' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('The device hub did not answer'));
    expect(screen.getByRole('button', { name: 'Lock it' })).toBeEnabled();
  });

  it('opens a web link in a new tab and an in-app path in the app; anything else is left out', () => {
    const onOpen = vi.fn();
    render(
      <NotificationActions
        actions={[
          { label: 'Open clip', type: 'view', url: 'https://cams.example.test/clip/1' },
          { label: 'Rooms', type: 'view', url: '/rooms' },
          { label: 'Bad', type: 'view', url: 'javascript:alert(1)' },
        ]}
        onOpen={onOpen}
      />,
    );
    const link = screen.getByRole('link', { name: 'Open clip' });
    expect(link).toHaveAttribute('href', 'https://cams.example.test/clip/1');
    expect(link).toHaveAttribute('target', '_blank');
    fireEvent.click(screen.getByRole('button', { name: 'Rooms' }));
    expect(onOpen).toHaveBeenCalledWith('/rooms');
    expect(screen.queryByText('Bad')).toBeNull();
  });
});
