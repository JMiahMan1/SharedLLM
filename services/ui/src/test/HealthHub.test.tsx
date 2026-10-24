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
      getHealthProfile: vi.fn(),
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
      readFoodLabelPhoto: vi.fn(),
    },
  };
});

// Pin "today" so the day that flows into every save call stays deterministic.
vi.mock('../lib/healthHub', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/healthHub')>()),
  localDay: () => '2026-10-22',
}));

const getHealthSummary = vi.mocked(api.getHealthSummary);
const getHealthFamily = vi.mocked(api.getHealthFamily);
const getHealthProfile = vi.mocked(api.getHealthProfile);
const getDailySteps = vi.mocked(api.getDailySteps);
const saveCheckin = vi.mocked(api.saveCheckin);
const addHealthMetrics = vi.mocked(api.addHealthMetrics);
const getSleep = vi.mocked(api.getSleep);
const logSleep = vi.mocked(api.logSleep);
const getNutritionDay = vi.mocked(api.getNutritionDay);
const addFood = vi.mocked(api.addFood);

const ALL_ON: HealthSummary['profile']['metrics'] = {
  readiness: true,
  checkin: true,
  sleep: true,
  nutrition: true,
  water: true,
  steps: true,
  active_minutes: true,
  streaks: true,
  weekly_digest: true,
  family_challenge: true,
  cheers: true,
  lessons: true,
  coach: true,
  fasting: false,
};

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
      metrics: ALL_ON,
      metric_catalog: [],
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
    cycle: null,
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
  getHealthProfile.mockResolvedValue(summary().profile);
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
      sources: ['openfoodfacts'],
      offline_sources: [],
      seed: { present: false, results: 0 },
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

  it('logs a seed food with a portion chip and shows the offline note', async () => {
    getNutritionDay.mockResolvedValue(emptyNutrition());
    vi.mocked(api.searchFood).mockResolvedValue({
      query: 'broccoli',
      sources: ['usda_fdc'],
      offline_sources: ['openfoodfacts'],
      seed: { present: true, results: 1 },
      results: [
        {
          code: 'sr_legacy:11090',
          name: 'Broccoli, raw',
          brand: '',
          image: null,
          serving_size: null,
          serving_grams: null,
          source: 'usda_fdc',
          portions: [
            { label: '1 cup, chopped', grams: 91 },
            { label: '1 stalk', grams: 151 },
          ],
          per_100g: {
            calories: 34,
            protein_g: 2.8,
            carbs_g: 6.6,
            fat_g: 0.4,
            fiber_g: 2.6,
            sugar_g: 1.7,
            sodium_mg: 33,
          },
        },
      ],
    });
    addFood.mockResolvedValue({} as never);

    renderWithClient(<HealthNutrition onNavigate={() => {}} />);

    const breakfast = await screen.findByTestId('meal-breakfast');
    fireEvent.click(within(breakfast).getByText('Add'));
    fireEvent.change(screen.getByLabelText('Search foods'), { target: { value: 'broccoli' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByTestId('food-search-offline')).toBeTruthy();
    fireEvent.click(await screen.findByText('Broccoli, raw'));
    fireEvent.click(screen.getByRole('button', { name: '1 cup, chopped' }));
    fireEvent.click(screen.getByTestId('confirm-add-food'));

    await waitFor(() => expect(addFood).toHaveBeenCalled());
    const body = addFood.mock.calls[0][0];
    expect(body).toMatchObject({
      day: '2026-10-22',
      meal: 'breakfast',
      name: 'Broccoli, raw',
      servings: 1,
      serving_grams: 91,
      source: 'usda_fdc',
    });
    expect(body.barcode).toBeNull();
    expect(body.calories).toBeCloseTo(30.94, 1);
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

describe('HealthToday family sharing', () => {
  it('shows a shared cycle phase with practical partner suggestions', async () => {
    getHealthSummary.mockResolvedValue(summary());
    getHealthFamily.mockResolvedValue({
      viewer: 'jeremiah',
      day: '2026-10-22',
      people: [
        {
          user_id: 'mom',
          scopes: ['cycle'],
          cycle: {
            applicable: true,
            enabled: true,
            phase: 'luteal',
            phase_label: 'Luteal',
            next_period_start: '2026-10-30',
            days_until_next_period: 8,
            confidence: 'medium',
            support: {
              phase: 'luteal',
              title: 'Before the next period',
              tips: ['Keep the plans flexible.', 'Offer quiet help.', 'Believe what she says.'],
            },
          },
        },
      ],
    });
    renderWithClient(<HealthToday onNavigate={() => {}} />);

    expect(await screen.findByText('Cycle: Luteal · next period in 8 days')).toBeInTheDocument();
    expect(screen.getByText('Before the next period')).toBeInTheDocument();
    expect(screen.getByText('Keep the plans flexible.')).toBeInTheDocument();
  });

  it('gives every shared scope its own line', async () => {
    getHealthSummary.mockResolvedValue(summary());
    getHealthFamily.mockResolvedValue({
      viewer: 'jeremiah',
      day: '2026-10-22',
      people: [
        {
          user_id: 'mom',
          scopes: ['cycle', 'nutrition', 'sleep'],
          sleep: { duration_minutes: 450, score: 81, label: 'good', logged: true },
          nutrition: { logged: true, calories: 1420 },
          cycle: {
            applicable: true,
            enabled: true,
            phase: 'follicular',
            phase_label: 'Follicular',
            next_period_start: '2026-11-10',
            days_until_next_period: 19,
            confidence: 'medium',
            support: null,
          },
        },
        {
          user_id: 'sam',
          scopes: ['nutrition'],
          nutrition: { logged: true, calories: null },
        },
      ],
    });
    renderWithClient(<HealthToday onNavigate={() => {}} />);

    const mom = await screen.findByTestId('shared-mom');
    expect(within(mom).getByText('Slept 7h 30m · 81/100')).toBeInTheDocument();
    expect(within(mom).getByText('1,420 kcal logged')).toBeInTheDocument();
    expect(within(mom).getByText('Cycle: Follicular · next period in 19 days')).toBeInTheDocument();
    // A kid-mode viewer gets no number from the server, so none is drawn.
    expect(within(screen.getByTestId('shared-sam')).getByText('Food logged today')).toBeInTheDocument();
  });
});

describe('Health cards switched off, and kid mode', () => {
  it('does not render a card the person switched off', async () => {
    const base = summary();
    getHealthSummary.mockResolvedValue(
      summary({ profile: { ...base.profile, metrics: { ...ALL_ON, water: false, readiness: false } } }),
    );
    renderWithClient(<HealthToday onNavigate={() => {}} />);

    expect(await screen.findByTestId('today-sleep-card')).toBeInTheDocument();
    expect(screen.queryByTestId('today-water-card')).not.toBeInTheDocument();
    expect(screen.queryByTestId('readiness-card')).not.toBeInTheDocument();
  });

  it('shows a child what was logged and never a calorie number', async () => {
    const base = summary();
    getHealthSummary.mockResolvedValue(
      summary({
        profile: { ...base.profile, kid_mode: true },
        nutrition: { ...emptyNutrition({ logged: true, entries_count: 2 }), totals: { calories: 640, protein_g: 20, carbs_g: 80, fat_g: 22, fiber_g: 4 } },
      }),
    );
    renderWithClient(<HealthToday onNavigate={() => {}} />);

    expect(await screen.findByTestId('today-nutrition-kid')).toHaveTextContent('2 things logged');
    expect(screen.queryByText(/640/)).not.toBeInTheDocument();
    expect(screen.queryByText(/kcal/)).not.toBeInTheDocument();
  });

  it('keeps calories, ranges and density colours off the kid-mode food day', async () => {
    getHealthProfile.mockResolvedValue({ ...summary().profile, kid_mode: true });
    getNutritionDay.mockResolvedValue(
      emptyNutrition({
        logged: true,
        entries_count: 1,
        totals: { calories: 250, protein_g: 5, carbs_g: 40, fat_g: 8, fiber_g: 2 },
        meals: {
          breakfast: {
            calories: 250,
            entries: [
              {
                id: 7,
                user_id: 'sam',
                day: '2026-10-22',
                meal: 'breakfast',
                name: 'Granola',
                brand: '',
                calories: 250,
                protein_g: 5,
                carbs_g: 40,
                fat_g: 8,
                fiber_g: 2,
                sugar_g: null,
                sodium_mg: null,
                servings: 1,
                serving_grams: 60,
                barcode: null,
                recipe_path: null,
                source: 'manual',
                external_id: null,
                created_at: '2026-10-22T08:00:00Z',
                density: { band: 'orange', label: 'calorie dense', calories_per_100g: 416 },
              },
            ],
          },
        },
      }),
    );
    renderWithClient(<HealthNutrition onNavigate={() => {}} />);

    expect(await screen.findByTestId('nutrition-kid-summary')).toHaveTextContent('1 thing logged');
    expect(screen.getByText('Granola')).toBeInTheDocument();
    expect(screen.queryByText(/kcal/)).not.toBeInTheDocument();
    expect(screen.queryByText('orange')).not.toBeInTheDocument();
  });
});

describe('Photo and voice quick add', () => {
  it('fills the By hand form from a label photo and saves only when added', async () => {
    getNutritionDay.mockResolvedValue(emptyNutrition());
    vi.mocked(api.readFoodLabelPhoto).mockResolvedValue({
      text: 'Calories 230\nTotal Fat 8g\nTotal Carbohydrate 37g',
      draft: {
        calories: 230, protein_g: null, carbs_g: 37, fat_g: 8, fiber_g: 4, sugar_g: null, sodium_mg: 160,
        serving_label: '2/3 cup', serving_grams: 55,
      },
      found: ['calories', 'carbs_g', 'fat_g', 'fiber_g', 'sodium_mg'],
      missing: ['protein_g'],
      per_100g: false,
    });
    addFood.mockResolvedValue({} as never);
    renderWithClient(<HealthNutrition onNavigate={() => {}} />);

    fireEvent.click(within(await screen.findByTestId('meal-snack')).getByText('Add'));
    fireEvent.click(screen.getByRole('tab', { name: 'Photo' }));
    const photo = new File(['jpeg'], 'label.jpg', { type: 'image/jpeg' });
    fireEvent.change(screen.getByLabelText('Nutrition label photo'), { target: { files: [photo] } });

    expect(await screen.findByTestId('photo-draft-note')).toHaveTextContent('Not on the label: protein');
    expect(addFood).not.toHaveBeenCalled();
    fireEvent.change(screen.getByPlaceholderText('Food name'), { target: { value: 'Granola' } });
    fireEvent.click(screen.getByText('Add food'));
    await waitFor(() =>
      expect(addFood).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'Granola', calories: 230, protein_g: 0, fat_g: 8, serving_grams: 55, source: 'photo' }),
      ),
    );
  });

  it('turns a typed sentence into a draft of matches and logs only on request', async () => {
    getNutritionDay.mockResolvedValue(emptyNutrition());
    vi.mocked(api.searchFood).mockImplementation(async (query: string) => ({
      query,
      sources: ['usda_fdc'],
      offline_sources: [],
      seed: { present: true, results: 1 },
      results: [
        {
          code: `fdc-${query}`,
          name: query === 'eggs' ? 'Egg, whole, cooked' : 'Bread, toasted',
          brand: '',
          image: null,
          serving_size: null,
          serving_grams: null,
          source: 'usda_fdc',
          portions: [{ label: '1 large', grams: query === 'eggs' ? 50 : 30 }],
          per_100g: { calories: query === 'eggs' ? 155 : 290, protein_g: 10, carbs_g: 1, fat_g: 10, fiber_g: null, sugar_g: null, sodium_mg: null },
        },
      ],
    }));
    addFood.mockResolvedValue({} as never);
    renderWithClient(<HealthNutrition onNavigate={() => {}} />);

    fireEvent.click(within(await screen.findByTestId('meal-breakfast')).getByText('Add'));
    fireEvent.click(screen.getByRole('tab', { name: 'Voice' }));
    fireEvent.change(screen.getByLabelText('What you ate'), { target: { value: 'two eggs and a slice of toast' } });
    fireEvent.click(screen.getByText('Find these foods'));

    expect(await screen.findByTestId('voice-food-draft')).toBeInTheDocument();
    expect(screen.getByLabelText('Servings for two eggs')).toHaveValue(2);
    expect(addFood).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('voice-food-log'));
    await waitFor(() => expect(addFood).toHaveBeenCalledTimes(2));
    expect(addFood.mock.calls[0][0]).toMatchObject({ name: 'Egg, whole, cooked', servings: 2, serving_grams: 50, source: 'voice', meal: 'breakfast' });
  });
});
