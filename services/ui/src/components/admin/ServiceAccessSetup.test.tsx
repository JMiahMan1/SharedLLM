import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ServiceAccessSetup from './ServiceAccessSetup';
import { api } from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), setUpServiceToken: vi.fn() },
  };
});

const setUp = vi.mocked(api.setUpServiceToken);

describe('ServiceAccessSetup', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('offers Nextcloud with no password and the other services with one', () => {
    render(<ServiceAccessSetup username="mom" services={['Nextcloud', 'Home Assistant']} />);
    expect(screen.getByRole('button', { name: /set up nextcloud/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /set up home assistant/i })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /set up audiobookshelf/i })).toBeNull();
  });

  it('mints a Nextcloud app password without asking for anything', async () => {
    setUp.mockResolvedValue({ success: true, username: 'mom', service: 'nextcloud', message: 'stored' });
    render(<ServiceAccessSetup username="mom" services={['Nextcloud']} />);

    fireEvent.click(screen.getByRole('button', { name: /set up nextcloud/i }));

    await waitFor(() =>
      expect(setUp).toHaveBeenCalledWith('mom', 'nextcloud', undefined),
    );
    expect(await screen.findByRole('button', { name: /nextcloud ready/i })).toBeTruthy();
  });

  it('asks for the password once, inline, and trades it for a token', async () => {
    setUp.mockResolvedValue({ success: true, username: 'mom', service: 'home_assistant', message: 'stored' });
    render(<ServiceAccessSetup username="mom" services={['Home Assistant']} />);

    fireEvent.click(screen.getByRole('button', { name: /set up home assistant/i }));
    const input = screen.getByLabelText('home_assistant password');
    expect(input.getAttribute('type')).toBe('password');
    fireEvent.change(input, { target: { value: 'typed-once' } });
    fireEvent.click(screen.getByRole('button', { name: /create token/i }));

    await waitFor(() => expect(setUp).toHaveBeenCalledWith('mom', 'home_assistant', 'typed-once'));
    expect(await screen.findByRole('button', { name: /home assistant ready/i })).toBeTruthy();
  });

  it('cancelling the password form calls nothing', () => {
    render(<ServiceAccessSetup username="mom" services={['Home Assistant']} />);

    fireEvent.click(screen.getByRole('button', { name: /set up home assistant/i }));
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));

    expect(setUp).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('home_assistant password')).toBeNull();
  });

  it('refuses to submit an empty password', () => {
    render(<ServiceAccessSetup username="mom" services={['Home Assistant']} />);

    fireEvent.click(screen.getByRole('button', { name: /set up home assistant/i }));
    fireEvent.click(screen.getByRole('button', { name: /create token/i }));

    expect(setUp).not.toHaveBeenCalled();
  });

  it('surfaces an upstream refusal instead of pretending it worked', async () => {
    setUp.mockResolvedValue({
      success: false,
      username: 'mom',
      service: 'home_assistant',
      message: 'failed',
      detail: 'Home Assistant rejected the login (400)',
    });
    render(<ServiceAccessSetup username="mom" services={['Home Assistant']} />);

    fireEvent.click(screen.getByRole('button', { name: /set up home assistant/i }));
    fireEvent.change(screen.getByLabelText('home_assistant password'), { target: { value: 'wrong' } });
    fireEvent.click(screen.getByRole('button', { name: /create token/i }));

    await waitFor(() => expect(setUp).toHaveBeenCalled());
    expect(screen.getByRole('button', { name: /set up home assistant/i })).toBeTruthy();
  });
});
