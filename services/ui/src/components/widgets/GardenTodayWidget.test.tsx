import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import GardenTodayWidget from './GardenTodayWidget';
import { api } from '../../services/api';
import type { GardenTodaySummary } from '../../types/api';
import type { UserWidgetSettings } from '../../types/widget';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      gardenToday: vi.fn(),
    },
  };
});

function todaySummary(dueCount = 2, overdueCount = 1): GardenTodaySummary {
  return {
    date: '2026-11-19',
    due_count: dueCount,
    overdue_count: overdueCount,
    by_room: {},
    all: [],
  } as unknown as GardenTodaySummary;
}

const SETTINGS = { widget_key: 'garden_today', size: 'medium' } as unknown as UserWidgetSettings;

function renderWidget() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <GardenTodayWidget settingsButton={null} userSettings={SETTINGS} onTogglePin={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('GardenTodayWidget', () => {
  it('shows due and overdue plant counts', async () => {
    vi.mocked(api.gardenToday).mockResolvedValue(todaySummary(2, 1));
    renderWidget();
    expect(await screen.findByText('2 due today')).toBeInTheDocument();
    expect(screen.getByText('1 overdue')).toBeInTheDocument();
  });

  it('shows all watered when nothing is due', async () => {
    vi.mocked(api.gardenToday).mockResolvedValue(todaySummary(0, 0));
    renderWidget();
    expect(await screen.findByText('All plants watered')).toBeInTheDocument();
  });
});
