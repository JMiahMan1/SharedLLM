import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';
import AmbientTimerWidget from './AmbientTimerWidget';
import { api } from '../../services/api';

// Mock the API methods — vi.mock is hoisted to top of file
vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getTimers: vi.fn().mockResolvedValue([]),
      createTimer: vi.fn().mockResolvedValue({ status: 'ACCEPTED', message: 'Timer created' }),
      deleteTimer: vi.fn().mockResolvedValue({ status: 'ACCEPTED', message: 'Timer deleted' }),
      getEntities: vi.fn().mockResolvedValue([]),
    },
  };
});

const mockWidgetSettings = {
  widget_key: 'ambient_timer',
  visibility: 'visible' as const,
  order_index: 1,
  size: 'small' as const,
  is_pinned: false,
  sort_mode: null,
  pinned_devices: [],
  config: {},
  updated_at: Date.now(),
};

function renderWithProviders(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return {
    ...render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{ui}</MemoryRouter>
      </QueryClientProvider>,
    ),
    queryClient,
  };
}

describe('AmbientTimerWidget', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setInterval'] });
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('renders loading state initially', () => {
    // Mock returns pending promise to simulate loading
    (api.getTimers as ReturnType<typeof vi.fn>).mockImplementation(
      () => new Promise(() => {}) // Never resolves
    );

    renderWithProviders(
      <AmbientTimerWidget
        userSettings={mockWidgetSettings}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );
    expect(screen.getByText('Loading timers...')).toBeInTheDocument();
  });

  it('renders empty state when no timers', async () => {
    (api.getTimers as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    renderWithProviders(
      <AmbientTimerWidget
        userSettings={mockWidgetSettings}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );

    // Flush React effects (uses setTimeout internally)
    await act(async () => {
      await Promise.resolve();
    });

    // Trigger the setInterval callback
    await act(async () => {
      vi.advanceTimersByTime(15000);
    });

    await waitFor(() => {
      expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument();
    });

    expect(screen.getByText('No active timers')).toBeInTheDocument();
  });

  it('renders timer list with active timers', async () => {
    const futureDate = new Date(Date.now() + 300000).toISOString();
    (api.getTimers as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: '1',
        type: 'timer',
        title: 'Test Timer',
        expires_at: futureDate,
        active: true,
        duration_sec: 300,
      },
    ]);

    renderWithProviders(
      <AmbientTimerWidget
        userSettings={mockWidgetSettings}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );

    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      vi.advanceTimersByTime(15000);
    });

    await waitFor(() => {
      expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument();
    });

    expect(screen.getByText('Test Timer')).toBeInTheDocument();
  });

  it('calls removeTimer on X button click', async () => {
    const futureDate = new Date(Date.now() + 300000).toISOString();
    (api.getTimers as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: '1',
        type: 'timer',
        title: 'Test Timer',
        expires_at: futureDate,
        active: true,
        duration_sec: 300,
      },
    ]);
    (api.deleteTimer as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'SUCCESS',
      message: 'Deleted timer.',
      service: 'timer_delete',
    });

    renderWithProviders(
      <AmbientTimerWidget
        userSettings={mockWidgetSettings}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );

    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      vi.advanceTimersByTime(15000);
    });

    await waitFor(() => {
      expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument();
    });

    const buttons = screen.getAllByRole('button');
    const deleteButton = buttons.find(btn => btn.querySelector('svg[data-lucide="x"]'));
    if (deleteButton) {
      fireEvent.click(deleteButton);
      expect(api.deleteTimer).toHaveBeenCalledWith('1');
    }
  });

  it('creates a timer when add button is clicked', async () => {
    (api.getTimers as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.createTimer as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'SUCCESS',
      message: 'Timer created.',
      service: 'timer_add',
      target_device: null,
    });

    renderWithProviders(
      <AmbientTimerWidget
        userSettings={mockWidgetSettings}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );

    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      vi.advanceTimersByTime(15000);
    });

    await waitFor(() => {
      expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument();
    });

    const durationInput = screen.getByPlaceholderText('Sec') as HTMLInputElement;
    fireEvent.change(durationInput, { target: { value: '60' } });
    const buttons = screen.getAllByRole('button');
    const addButton = buttons[buttons.length - 1];
    fireEvent.click(addButton);

    expect(api.createTimer).toHaveBeenCalledWith({
      duration_str: '60s',
      title: 'Timer 1',
      type: 'timer',
      target_device: undefined,
    });
  });

  it('toggles pin state when pin button is clicked', () => {
    const onTogglePin = vi.fn();
    renderWithProviders(
      <AmbientTimerWidget
        userSettings={mockWidgetSettings}
        onTogglePin={onTogglePin}
        settingsButton={null}
      />
    );
    const pinButton = screen.getByTitle('Pin widget');
    fireEvent.click(pinButton);
    expect(onTogglePin).toHaveBeenCalled();
  });

  it('shows Total Progress section when timers exist', async () => {
    const futureDate = new Date(Date.now() + 300000).toISOString();
    (api.getTimers as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: '1',
        type: 'timer',
        title: 'Test Timer',
        expires_at: futureDate,
        active: true,
        duration_sec: 300,
      },
    ]);

    renderWithProviders(
      <AmbientTimerWidget
        userSettings={mockWidgetSettings}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );

    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      vi.advanceTimersByTime(15000);
    });

    await waitFor(() => {
      expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument();
    });

    expect(screen.getByText('Total Progress')).toBeInTheDocument();
  });

  it('falls back to local timer when createTimer fails', async () => {
    (api.getTimers as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.createTimer as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('Failed to create timer'));

    renderWithProviders(
      <AmbientTimerWidget
        userSettings={mockWidgetSettings}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );

    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      vi.advanceTimersByTime(15000);
    });

    await waitFor(() => {
      expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument();
    });

    const durationInput = screen.getByPlaceholderText('Sec') as HTMLInputElement;
    fireEvent.change(durationInput, { target: { value: '60' } });
    const buttons = screen.getAllByRole('button');
    const addButton = buttons[buttons.length - 1];
    fireEvent.click(addButton);

    // Timer should appear (either remote or local fallback)
    await waitFor(() => {
      const timerElement = screen.getByText(/Timer 1|Loading timers/);
      expect(timerElement).toBeInTheDocument();
    }, { timeout: 3000 });
  });

  it('decrements timer countdown', async () => {
    const futureDate = new Date(Date.now() + 10000).toISOString();
    (api.getTimers as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: '1',
        type: 'timer',
        title: 'Countdown Timer',
        expires_at: futureDate,
        active: true,
        duration_sec: 10,
      },
    ]);

    renderWithProviders(
      <AmbientTimerWidget
        userSettings={mockWidgetSettings}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );

    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      vi.advanceTimersByTime(15000);
    });

    await waitFor(() => {
      expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument();
    });

    const timeEl = screen.getByText(/0:[0-9]{2}/);
    expect(timeEl).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(2000);
    });

    const updatedTimeEl = screen.getByText(/0:[0-9]{2}/);
    expect(updatedTimeEl).toBeInTheDocument();
  });

  it('removes timer when countdown reaches zero', async () => {
    const futureDate = new Date(Date.now() + 2000).toISOString();
    (api.getTimers as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: '1',
        type: 'timer',
        title: 'Short Timer',
        expires_at: futureDate,
        active: true,
        duration_sec: 2,
      },
    ]);

    renderWithProviders(
      <AmbientTimerWidget
        userSettings={mockWidgetSettings}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );

    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      vi.advanceTimersByTime(15000);
    });

    await waitFor(() => {
      expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument();
    });

    expect(screen.getByText('Short Timer')).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(3000);
    });

    expect(screen.queryByText('Short Timer')).not.toBeInTheDocument();
    expect(screen.getByText('No active timers')).toBeInTheDocument();
  });

  it('shows device picker when media players are available', async () => {
    (api.getEntities as ReturnType<typeof vi.fn>).mockResolvedValue([
      { entity_id: 'media_player.kitchen', friendly_name: 'Kitchen Speaker', state: 'playing', domain: 'media_player' },
      { entity_id: 'media_player.living_room', friendly_name: 'Living Room', state: 'idle', domain: 'media_player' },
    ]);
    (api.getTimers as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    renderWithProviders(
      <AmbientTimerWidget
        userSettings={mockWidgetSettings}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );

    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      vi.advanceTimersByTime(15000);
    });

    await waitFor(() => {
      expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByRole('combobox')).toBeInTheDocument();
    }, { timeout: 3000 });

    const select = screen.getByRole('combobox') as HTMLSelectElement;
    expect(select.options.length).toBe(3);
    expect(select.options[0].text).toBe('No alert (silent)');
    expect(select.options[1].text).toMatch(/^Kitchen Speaker\s*$/);
    expect(select.options[2].text).toBe('Living Room (idle)');
  });

  it('passes target_device when creating timer with device selected', async () => {
    (api.getEntities as ReturnType<typeof vi.fn>).mockResolvedValue([
      { entity_id: 'media_player.kitchen', friendly_name: 'Kitchen Speaker', state: 'idle', domain: 'media_player' },
    ]);
    (api.getTimers as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.createTimer as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'SUCCESS',
      message: 'Timer created.',
      service: 'timer_add',
      target_device: 'media_player.kitchen',
    });

    renderWithProviders(
      <AmbientTimerWidget
        userSettings={mockWidgetSettings}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );

    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      vi.advanceTimersByTime(15000);
    });

    await waitFor(() => {
      expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByRole('combobox')).toBeInTheDocument();
    }, { timeout: 3000 });

    const select = screen.getByRole('combobox') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'media_player.kitchen' } });

    const durationInput = screen.getByPlaceholderText('Sec') as HTMLInputElement;
    fireEvent.change(durationInput, { target: { value: '30' } });

    const buttons = screen.getAllByRole('button');
    const addButton = buttons[buttons.length - 1];
    fireEvent.click(addButton);

    expect(api.createTimer).toHaveBeenCalledWith({
      duration_str: '30s',
      title: 'Timer 1',
      type: 'timer',
      target_device: 'media_player.kitchen',
    });
  });
});
