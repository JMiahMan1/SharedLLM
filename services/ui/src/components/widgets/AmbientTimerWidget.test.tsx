import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';
import AmbientTimerWidget from './AmbientTimerWidget';
import { api } from '../../services/api';
import { playChime } from '../../lib/chime';

vi.mock('../../lib/chime', () => ({ playChime: vi.fn(), unlockChime: vi.fn() }));

// Mock the API methods (vi.mock is hoisted to top of file)
vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getTimers: vi.fn().mockResolvedValue([]),
      getTimerDevices: vi.fn().mockResolvedValue({ devices: [] }),
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
    (api.getTimerDevices as ReturnType<typeof vi.fn>).mockResolvedValue({ devices: [] });
    (api.getEntities as ReturnType<typeof vi.fn>).mockResolvedValue([]);
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

    const deleteButton = screen.getByRole('button', { name: 'Delete Test Timer' });
    fireEvent.click(deleteButton);
    expect(api.deleteTimer).toHaveBeenCalledWith('Test Timer', 'timer', '1');
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

  it('keeps a paused server timer on screen so it can be resumed', async () => {
    // Nothing here counts down, and findBy* polls with setInterval: with it
    // faked the query only re-checks on DOM mutations and can miss the render
    // when the whole suite is running.
    vi.useRealTimers();
    const now = Date.now();
    (api.getTimers as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: 'p1',
        type: 'timer',
        title: 'Pasta',
        expires_at: new Date(now + 240_000).toISOString(),
        paused_at: new Date(now - 60_000).toISOString(),
        active: false,
        duration_sec: 600,
      },
    ]);

    renderWithProviders(
      <AmbientTimerWidget userSettings={mockWidgetSettings} onTogglePin={vi.fn()} settingsButton={null} />
    );

    expect(await screen.findByText('Pasta')).toBeInTheDocument();
    expect(screen.getByText('Paused')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resume' })).toBeInTheDocument();
    // Remaining is frozen at the pause: 5 minutes, not 4.
    expect(screen.getByText('5:00')).toBeInTheDocument();
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
    // It chimed once when it finished, not on every tick after.
    expect(playChime).toHaveBeenCalledTimes(1);
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
    expect(select.options[1].text).toBe('Kitchen Speaker');
    expect(select.options[2].text).toBe('Living Room');
  });

  it('uses the classified timer devices instead of raw media players', async () => {
    (api.getTimerDevices as ReturnType<typeof vi.fn>).mockResolvedValue({
      devices: [
        {
          id: 'media_player.office_tv',
          name: 'Office TV',
          kind: 'tv',
          has_speaker: true,
          has_screen: true,
          announce_capable: true,
          state: 'idle',
        },
        {
          id: 'media_player.office_tv_remote',
          name: 'Office TV Remote',
          kind: 'tv',
          has_speaker: true,
          has_screen: true,
          announce_capable: false,
          state: 'on',
        },
        {
          id: 'esphome:watch',
          name: 'Jarvis Watch',
          kind: 'watch',
          has_speaker: false,
          has_screen: true,
        },
      ],
    });
    (api.getEntities as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        entity_id: 'media_player.' + ('id_1_username_default_display_name_'.repeat(34)),
        friendly_name: '{"id":1,"username":"default"}',
        state: 'unavailable',
        domain: 'media_player',
      },
      { entity_id: 'media_player.office_tv_cast', friendly_name: 'Office TV Cast', state: 'idle', domain: 'media_player' },
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

    await waitFor(() => {
      expect(screen.getByRole('combobox')).toBeInTheDocument();
    }, { timeout: 3000 });

    const select = screen.getByRole('combobox') as HTMLSelectElement;
    expect(Array.from(select.options, (option) => option.text)).toEqual([
      'No alert (silent)',
      'Office TV',
    ]);
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

  it('supports emoji sounds, household scope, multi-device targets, and ESPHome screen flash', async () => {
    vi.useRealTimers();
    (api.getEntities as ReturnType<typeof vi.fn>).mockResolvedValue([
      { entity_id: 'media_player.kitchen', friendly_name: 'Kitchen Speaker', state: 'idle', domain: 'media_player' },
    ]);
    (api.getTimers as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.createTimer as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'SUCCESS',
      message: 'Timer created.',
      service: 'timer_add',
      detail: { timer_id: 'egg-1' },
    });

    renderWithProviders(
      <AmbientTimerWidget
        userSettings={mockWidgetSettings}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );

    const optionsBtn = await screen.findByRole('button', { name: 'Timer & alarm options' });
    fireEvent.click(optionsBtn);

    // Pick 3m Egg preset
    fireEvent.click(screen.getByRole('button', { name: /3m 🥚/i }));
    // Toggle Household scope
    fireEvent.click(screen.getByRole('button', { name: /Personal/i }));
    // Select alarm melody
    fireEvent.click(screen.getByRole('button', { name: 'Kitchen Egg Timer' }));
    // Select both Mobile App and Kitchen Speaker
    fireEvent.click(screen.getByRole('button', { name: /Mobile App/i }));
    fireEvent.click(screen.getByRole('button', { name: /Kitchen Speaker/i }));

    fireEvent.click(screen.getByRole('button', { name: 'Add timer' }));

    await waitFor(() => {
      expect(api.createTimer).toHaveBeenCalledWith({
        title: 'Soft Boiled Egg',
        duration_str: '180s',
        type: 'timer',
        target_device: 'phone',
        target_devices: ['phone', 'media_player.kitchen'],
        scope: 'household',
        emoji: '🥚',
        sound: 'egg_timer',
      });
    });
  });
});

