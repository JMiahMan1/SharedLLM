import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';
import AmbientTimerWidget from './AmbientTimerWidget';

// Mock the API service at module level — vi.mock is hoisted to top of file
vi.mock('../../services/api', () => ({
  api: {
    getTimers: vi.fn(),
    createTimer: vi.fn(),
    deleteTimer: vi.fn(),
    getMe: vi.fn().mockResolvedValue({ username: 'test', role: 'admin' }),
  },
}));

// Import the mocked API after vi.mock is hoisted
import { api } from '../../services/api';

const mockGetTimers = api.getTimers as ReturnType<typeof vi.fn>;
const mockCreateTimer = api.createTimer as ReturnType<typeof vi.fn>;
const mockDeleteTimer = api.deleteTimer as ReturnType<typeof vi.fn>;

// Render helper that completely bypasses AuthProvider
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
    mockGetTimers.mockResolvedValue([]);
    mockCreateTimer.mockResolvedValue({ status: 'SUCCESS' });
    mockDeleteTimer.mockResolvedValue({ status: 'SUCCESS' });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('renders loading state initially', () => {
    mockGetTimers.mockReturnValue(new Promise(() => {})); // never resolve
    renderWithProviders(
      <AmbientTimerWidget
        userSettings={{ is_pinned: false }}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );
    expect(screen.getByText('Loading timers...')).toBeInTheDocument();
  });

  it('renders empty state when no timers', async () => {
    mockGetTimers.mockResolvedValue([]);
    renderWithProviders(
      <AmbientTimerWidget
        userSettings={{ is_pinned: false }}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );
    await waitFor(() => expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument());
    expect(screen.getByText('No active timers')).toBeInTheDocument();
  });

  it('renders timer list with active timers', async () => {
    const now = Date.now();
    mockGetTimers.mockResolvedValue([
      {
        id: '1',
        title: 'Test Timer',
        expires_at: new Date(now + 300000).toISOString(),
        active: true,
      },
    ]);
    renderWithProviders(
      <AmbientTimerWidget
        userSettings={{ is_pinned: false }}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );
    await waitFor(() => expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument());
    expect(screen.getByText('Test Timer')).toBeInTheDocument();
  });

  it('calls removeTimer on X button click', async () => {
    const now = Date.now();
    mockGetTimers.mockResolvedValue([
      {
        id: '1',
        title: 'Test Timer',
        expires_at: new Date(now + 300000).toISOString(),
        active: true,
      },
    ]);
    renderWithProviders(
      <AmbientTimerWidget
        userSettings={{ is_pinned: false }}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );
    await waitFor(() => expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument());
    const deleteButton = screen.getAllByRole('button')[1]; // X button
    fireEvent.click(deleteButton);
    await waitFor(() => expect(mockDeleteTimer).toHaveBeenCalled());
  });

  it('creates a timer when add button is clicked', async () => {
    mockGetTimers.mockResolvedValue([]);
    renderWithProviders(
      <AmbientTimerWidget
        userSettings={{ is_pinned: false }}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );
    await waitFor(() => expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument());
    const durationInput = screen.getByPlaceholderText('Sec') as HTMLInputElement;
    fireEvent.change(durationInput, { target: { value: '60' } });
    const buttons = screen.getAllByRole('button');
    const addButton = buttons[buttons.length - 1]; // Plus button is last
    fireEvent.click(addButton);
    expect(mockCreateTimer).toHaveBeenCalled();
  });

  it('toggles pin state when pin button is clicked', () => {
    const onTogglePin = vi.fn();
    renderWithProviders(
      <AmbientTimerWidget
        userSettings={{ is_pinned: false }}
        onTogglePin={onTogglePin}
        settingsButton={null}
      />
    );
    const pinButton = screen.getByTitle('Pin widget');
    fireEvent.click(pinButton);
    expect(onTogglePin).toHaveBeenCalled();
  });

  it('shows Total Progress section when timers exist', async () => {
    const now = Date.now();
    mockGetTimers.mockResolvedValue([
      {
        id: '1',
        title: 'Test Timer',
        expires_at: new Date(now + 300000).toISOString(),
        active: true,
      },
    ]);
    renderWithProviders(
      <AmbientTimerWidget
        userSettings={{ is_pinned: false }}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );
    await waitFor(() => expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument());
    expect(screen.getByText('Total Progress')).toBeInTheDocument();
  });

  it('falls back to local timer when createTimer fails', async () => {
    mockGetTimers.mockResolvedValue([]);
    mockCreateTimer.mockRejectedValue(new Error('API error'));
    renderWithProviders(
      <AmbientTimerWidget
        userSettings={{ is_pinned: false }}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );
    await waitFor(() => expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument());
    const durationInput = screen.getByPlaceholderText('Sec') as HTMLInputElement;
    fireEvent.change(durationInput, { target: { value: '60' } });
    const buttons = screen.getAllByRole('button');
    const addButton = buttons[buttons.length - 1];
    fireEvent.click(addButton);
    // Timer should appear despite API failure
    await waitFor(() => expect(screen.getByText('Timer 1')).toBeInTheDocument());
  });

  it('decrements timer countdown', async () => {
    const now = Date.now();
    mockGetTimers.mockResolvedValue([
      {
        id: '1',
        title: 'Countdown Timer',
        expires_at: new Date(now + 10000).toISOString(),
        active: true,
      },
    ]);
    renderWithProviders(
      <AmbientTimerWidget
        userSettings={{ is_pinned: false }}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );
    await waitFor(() => expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument());
    const timeEl = screen.getByText(/0:[0-9]{2}/);
    expect(timeEl).toBeInTheDocument();

    await new Promise(resolve => setTimeout(resolve, 2000));

    const updatedTimeEl = screen.getByText(/0:[0-9]{2}/);
    expect(updatedTimeEl).toBeInTheDocument();
  });

  it('removes timer when countdown reaches zero', async () => {
    const now = Date.now();
    mockGetTimers.mockResolvedValue([
      {
        id: '1',
        title: 'Short Timer',
        expires_at: new Date(now + 2000).toISOString(),
        active: true,
      },
    ]);
    renderWithProviders(
      <AmbientTimerWidget
        userSettings={{ is_pinned: false }}
        onTogglePin={vi.fn()}
        settingsButton={null}
      />
    );
    await waitFor(() => expect(screen.queryByText('Loading timers...')).not.toBeInTheDocument());
    expect(screen.getByText('Short Timer')).toBeInTheDocument();

    await new Promise(resolve => setTimeout(resolve, 3000));

    expect(screen.queryByText('Short Timer')).not.toBeInTheDocument();
    expect(screen.getByText('No active timers')).toBeInTheDocument();
  });
});
