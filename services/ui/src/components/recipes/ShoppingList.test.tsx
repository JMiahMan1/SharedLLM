import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ShoppingList from './ShoppingList';
import { api } from '../../services/api';
import { readShoppingList } from '../../lib/shoppingListStore';

vi.mock('../../lib/shoppingListStore', () => ({
  readShoppingList: vi.fn(),
  saveShoppingList: vi.fn().mockResolvedValue({ response: { status: 'SUCCESS', message: '' } }),
  mirrorSkylightList: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      gardenHarvestReady: vi.fn(),
    },
  };
});

const m = vi.mocked(api);
const readMock = vi.mocked(readShoppingList);

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  m.gardenHarvestReady.mockResolvedValue({ count: 0, ready: [] });
});

function renderShoppingList(props?: Partial<Parameters<typeof ShoppingList>[0]>) {
  const queryClient = makeQueryClient();
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ShoppingList
          asUser={undefined}
          skylightOff={false}
          onShare={vi.fn()}
          onGoToWeek={vi.fn()}
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, queryClient };
}

describe('ShoppingList garden badge single-toggle', () => {
  it('toggles checkbox once when clicking "In your garden" button', async () => {
    readMock.mockResolvedValueOnce({
      text: '- [ ] basil\n- [ ] tomatoes\n',
      exists: false,
    });
    m.gardenHarvestReady.mockResolvedValueOnce({
      count: 1,
      ready: [
        {
          plant_id: 'plant-1',
          nickname: 'Sir Basil',
          common_name: 'Sweet Basil',
          category: 'herb',
          species_ref: 'basil_sweet',
          culinary_aliases: ['basil'],
          in_cooldown: false,
        },
      ],
    });

    renderShoppingList();

    await waitFor(() => {
      expect(screen.getByText('basil')).toBeInTheDocument();
    });

    const checkbox = screen.getAllByRole('checkbox')[0];
    expect(checkbox).not.toBeChecked();

    // Click the "In your garden" badge button
    const gardenButton = screen.getByText('In your garden');
    fireEvent.click(gardenButton);

    await waitFor(() => {
      expect(checkbox).toBeChecked();
    });

    // The badge should disappear once checked
    await waitFor(() => {
      expect(screen.queryByText('In your garden')).not.toBeInTheDocument();
    });
  });

  it('does not double-toggle when clicking badge (single toggle only)', async () => {
    readMock.mockResolvedValueOnce({
      text: '- [ ] basil\n',
      exists: false,
    });
    m.gardenHarvestReady.mockResolvedValueOnce({
      count: 1,
      ready: [
        {
          plant_id: 'plant-1',
          nickname: 'Sir Basil',
          common_name: 'Sweet Basil',
          category: 'herb',
          species_ref: 'basil_sweet',
          culinary_aliases: ['basil'],
          in_cooldown: false,
        },
      ],
    });

    renderShoppingList();

    await waitFor(() => {
      expect(screen.getByText('basil')).toBeInTheDocument();
    });

    const checkbox = screen.getAllByRole('checkbox')[0];
    expect(checkbox).not.toBeChecked();

    const gardenButton = screen.getByText('In your garden');
    fireEvent.click(gardenButton);

    await waitFor(() => {
      expect(checkbox).toBeChecked();
    });

    // Verify it's checked (not toggled back off)
    expect(checkbox).toBeChecked();
  });

  it('does not show garden badge for non-garden items', async () => {
    readMock.mockResolvedValueOnce({
      text: '- [ ] milk\n- [ ] eggs\n',
      exists: false,
    });
    m.gardenHarvestReady.mockResolvedValueOnce({ count: 0, ready: [] });

    renderShoppingList();

    await waitFor(() => {
      expect(screen.getByText('milk')).toBeInTheDocument();
    });

    expect(screen.queryByText('In your garden')).not.toBeInTheDocument();
  });
});
