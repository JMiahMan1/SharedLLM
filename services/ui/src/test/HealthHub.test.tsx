import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import HealthToday from '../components/health/HealthToday';
import HealthSleep from '../components/health/HealthSleep';
import HealthNutrition from '../components/health/HealthNutrition';
import { api } from '../services/api';
import type { HealthSummary, NutritionDay } from '../types/api';

vi.mock('../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getHealthSummary: vi.fn(),
      getHealthFamily: vi.fn(),
      getDailySteps: vi.fn(),
      saveCheckin: vi.fn(),
      addHealthMetrics: vi.fn(),
      getSleep: vi.fn(),
      logSleep: vi.fn(),
      deleteSleep: vi.fn(),
      getNutritionDay: vi.fn(),
      searchFood: vi.fn(),
      lookupBarcode: vi.fn(),
      addFood: vi.fn(),
      deleteFood: vi.fn(),
    },
  };
});

const getHealthSummary = vi.mocked(api.getHealthSummary);
const getHealthFamily = vi.mocked(api.getHealthFamily);
const getDailySteps = vi.mocked(api.getDailySteps);
const saveCheckin = vi.mocked(api.saveCheckin);
const addHealthMetrics = vi.mocked(api.addHealthMetrics);
const getSleep = vi.mocked(api.getSleep);
const logSleep = vi.mocked(api.logSleep);
const getNutritionDay = vi.mocked(api.getNutritionDay);
const addFood = vi.mocked(api.addFood);

function summary(overrides: Partial<HealthSummary> = {}): HealthSummary {
  return {
    user_id: 'jeremiah',
    day: '2026-10-22',
    profile: {
      user_id: 'jeremiah',
      exists: true,
      display_name: 'Jeremiah',
      birth_year: 1985,
      height_cm: 180,
      sex: 'male',
      activity_level: 'moderate',
      goal: 'maintain',
      units: 'imperial',
      kid_mode: false,
      sleep_need_minutes: 480,
      water_goal_ml: 2000,
      weekly_active_minutes: 150,
    },
    defaults_used: [],
    sleep: {
      last_night: {
        id: 1,
        user_id: 'jeremiah',
        night: '2026-10-21',
        bedtime: '2026-10-20T22:30:00-07:00',
        wake_time: '2026-10-21T06:30:00-07:00',
        duration_minutes: 480,
        efficiency: 90,
        quality: 4,
        stages: {},
        source: 'manual',
        external_id: null,
        note: '',
      },
      score: {
        score: 92,
        label: 'rested',
        detail: 'A full night.',
        confidence: 'high',
        missing: [],
        components: {},
      },
      need_minutes: 480,
      history: [],
    },
    readiness: {
      score: 82,
      label: 'ready',
      detail: 'Green light. A normal day fits.',
      confidence: 'high',
      missing: [],
      components: {},
    },
    checkin: null,
    nutrition: {
      user_id: 'jeremiah',
      day: '2026-10-22',
      entries_count: 0,
      logged: false,
      totals: { calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0, fiber_g: 0 },
      meals: {},
      target: {
        kind: 'adult_range',
        min: 1800,
        max: 2000,
        bmr: 1700,
        tdee: 1900,
        goal: 'maintain',
        note: '',
        missing: [],
        deficit_allowed: false,
        protein_g: 120,
        carbs_g: 210,
        fat_g: 63,
      },
    },
    water: { ml: 500, goal_ml: 2000, percent: 25, logged: true },
    metrics: {},
    resting_hr_baseline: null,
    activity: null,
    alerts: [],
    ...overrides,
  };
}

function emptyNutrition(overrides: Partial<NutritionDay> = {}): NutritionDay {
  return {
    user_id: 'jeremiah',
    day: '2026-10-22',
    entries_count: 0,
    logged: false,
    totals: { calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0, fiber_g: 0 },
    meals: {},
    target: {
      kind: 'adult_range',
      min: 1800,
      max: 2000,
      bmr: 1700,
      tdee: 1900,
      goal: 'maintain',
      note: '',
      missing: [],
      deficit_allowed: false,
      protein_g: 120,
      carbs_g: 210,
      fat_g: 63,
    },
    ...overrides,
  };
}

function renderWithClient(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  getHealthFamily.mockResolvedValue({ viewer: 'jeremiah', day: '2026-10-22', people: [] });
  getDailySteps.mockResolvedValue({
    user_id: 'jeremiah',
    daily_steps: { '2026-10-22': 4200 },
    today: 4200,
    goal: 10000,
    sources: { phone: 4200 },
  });
  saveCheckin.mockResolvedValue({
    id: 1,
    user_id: 'jeremiah',
    day: '2026-10-22',
    energy: 4,
    mood: null,
    soreness: null,
    stress: null,
    note: '',
    updated_at: '2026-10-22T08:00:00Z',
  });
  addHealthMetrics.mockResolvedValue({ accepted: 1, duplicates: 0, samples: [] });
});

describe('HealthToday', () => {
  it('leads with the interpreted readiness score', async () => {
    getHealthSummary.mockResolvedValue(summary());
    renderWithClient(<HealthToday onNavigate={() => {}} />);

    const card = await screen.findByTestId('readiness-card');
    expect(within(card).getByText('82')).toBeInTheDocument();
    expect(within(card).getByText('ready')).toBeInTheDocument();
    expect(screen.getByText('4,200')).toBeInTheDocument();
  });

  it('offers profile setup instead of inventing a range', async () => {
    getHealthSummary.mockResolvedValue(
      summary({
        profile: {
          ...summary().profile,
          exists: false,
          birth_year: null,
          height_cm: null,
          sex: null,
          sleep_need_minutes: null,
          water_goal_ml: null,
        },
        defaults_used: ['sleep_need_minutes', 'water_goal_ml'],
        readiness: null,
        sleep: { last_night: null, score: null, need_minutes: 480, history: [] },
      }),
    );
    renderWithClient(<HealthToday onNavigate={() => {}} />);
    expect(await screen.findByTestId('health-profile-prompt')).toBeInTheDocument();
    expect(screen.getByText('Set up your health profile')).toBeInTheDocument();
  });

  it('saves a one-tap check-in', async () => {
    getHealthSummary.mockResolvedValue(summary());
    renderWithClient(<HealthToday onNavigate={() => {}} />);

    fireEvent.click(await screen.findByLabelText('Energy: Good'));
    fireEvent.click(screen.getByTestId('save-checkin'));

    await waitFor(() =>
      expect(saveCheckin).toHaveBeenCalledWith('2026-10-22', { energy: 4 }),
    );
  });

  it('logs water as a metric reading', async () => {
    getHealthSummary.mockResolvedValue(summary());
    renderWithClient(<HealthToday onNavigate={() => {}} />);

    fireEvent.click(await screen.findByTestId('add-water-500'));

    await waitFor(() => expect(addHealthMetrics).toHaveBeenCalled());
    const samples = addHealthMetrics.mock.calls[0][0];
    expect(samples[0]).toMatchObject({ metric: 'water', value: 500, unit: 'ml' });
  });
});

describe('HealthSleep', () => {
  it('saves a night from bedtime and wake time', async () => {
    getSleep.mockResolvedValue({ user_id: 'jeremiah', sessions: [] });
    getHealthSummary.mockResolvedValue(
      summary({ sleep: { last_night: null, score: null, need_minutes: 480, history: [] } }),
    );
    logSleep.mockResolvedValue({
      id: 9,
      user_id: 'jeremiah',
      night: '2026-10-22',
      bedtime: null,
      wake_time: null,
      duration_minutes: 450,
      efficiency: null,
      quality: 4,
      stages: {},
      source: 'manual',
      external_id: null,
      note: '',
      duplicate: false,
    });
    renderWithClient(<HealthSleep />);

    fireEvent.change(await screen.findByLabelText('Bedtime'), { target: { value: '22:30' } });
    fireEvent.change(screen.getByLabelText('Wake'), { target: { value: '06:30' } });
    fireEvent.click(screen.getByLabelText('Sleep quality: Good'));
    fireEvent.click(screen.getByTestId('save-sleep'));

    await waitFor(() =>
      expect(logSleep).toHaveBeenCalledWith(
        expect.objectContaining({ bedtime: '22:30', wake_time: '06:30', quality: 4 }),
      ),
    );
  });
});

describe('HealthNutrition', () => {
  it('adds a searched food with a portion', async () => {
    getNutritionDay.mockResolvedValue(emptyNutrition());
    vi.mocked(api.searchFood).mockResolvedValue({
      query: 'apple',
      source: 'openfoodfacts',
      results: [
        {
          code: '123',
          name: 'Apple',
          brand: 'Orchard',
          image: null,
          serving_size: '1 apple',
          serving_grams: 182,
          per_100g: {
            calories: 52,
            protein_g: 0.3,
            carbs_g: 14,
            fat_g: 0.2,
            fiber_g: 2.4,
            sugar_g: 10,
            sodium_mg: 1,
          },
        },
      ],
    });
    addFood.mockResolvedValue({} as never);

    renderWithClient(<HealthNutrition onNavigate={() => {}} />);

    const breakfast = await screen.findByTestId('meal-breakfast');
    fireEvent.click(within(breakfast).getByText('Add'));
    fireEvent.change(screen.getByLabelText('Search foods'), { target: { value: 'apple' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    fireEvent.click(await screen.findByText('Apple'));
    fireEvent.click(screen.getByTestId('confirm-add-food'));

    await waitFor(() => expect(addFood).toHaveBeenCalled());
    expect(addFood.mock.calls[0][0]).toMatchObject({
      day: '2026-10-22',
      meal: 'breakfast',
      name: 'Apple',
      servings: 1,
      serving_grams: 182,
      source: 'openfoodfacts',
    });
    expect(addFood.mock.calls[0][0].calories).toBeCloseTo(94.64, 1);
  });

  it('adds food by hand', async () => {
    getNutritionDay.mockResolvedValue(emptyNutrition());
    addFood.mockResolvedValue({} as never);
    renderWithClient(<HealthNutrition onNavigate={() => {}} />);

    const lunch = await screen.findByTestId('meal-lunch');
    fireEvent.click(within(lunch).getByText('Add'));
    fireEvent.click(screen.getByText('By hand'));
    fireEvent.change(screen.getByPlaceholderText('Food name'), { target: { value: 'Toast' } });
    fireEvent.change(screen.getByLabelText('Calories'), { target: { value: '120' } });
    fireEvent.click(screen.getByText('Add food'));

    await waitFor(() =>
      expect(addFood).toHaveBeenCalledWith(
        expect.objectContaining({ meal: 'lunch', name: 'Toast', calories: 120 }),
      ),
    );
  });
});
