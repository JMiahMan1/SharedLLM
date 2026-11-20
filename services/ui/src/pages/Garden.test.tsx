import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import React from 'react';
import Garden from '../pages/Garden';
import { api } from '../services/api';
import type { GardenSpecies } from '../types/api';
import { AuthProvider } from '../context/AuthContext';

vi.mock('../context/AuthContext', async (importActual) => {
  const actual = await importActual<typeof import('../context/AuthContext')>();
  return {
    ...actual,
    useAuth: () => ({ user: { username: 'mother', is_admin: true, family_role: 'mother' }, token: 'test' }),
  };
});

vi.mock('../services/api', () => ({
  api: {
    gardenToday: vi.fn(),
    gardenListPlants: vi.fn(),
    gardenListLocations: vi.fn(),
    gardenLogCare: vi.fn(),
    gardenSnooze: vi.fn(),
    gardenWaterAll: vi.fn(),
    gardenGetPlantLogs: vi.fn(),
    gardenSearch: vi.fn(),
    gardenListSpecies: vi.fn(),
    gardenSpeciesLookup: vi.fn(),
    gardenSaveCustomSpecies: vi.fn(),
    gardenCreatePlant: vi.fn(),
    gardenGetProfile: vi.fn(),
    gardenStreaks: vi.fn(),
  },
}));

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          {children}
        </AuthProvider>
      </QueryClientProvider>
    </BrowserRouter>
  );
};

describe('Garden Today tab', () => {
  let wrapper: ReturnType<typeof createWrapper>;

  beforeEach(() => {
    wrapper = createWrapper();
    vi.mocked(api.gardenToday).mockResolvedValue({
      date: '2026-11-19',
      due_count: 1,
      overdue_count: 0,
      by_room: {
        'Living Room': [
          {
            plant_id: 'plant-1',
            nickname: 'Monty',
            common_name: 'Monstera',
            scientific_name: 'Monstera deliciosa',
            category: 'houseplant',
            location_name: 'Living Room',
            location_id: 'loc-1',
            assigned_to: null,
            action: 'water',
            next_due_at: '2026-11-19T10:00:00Z',
            overdue: false,
            care_cue: 'Top-drain · top 2 in dry · tap water',
            watering_method: 'top_drain',
            dryness_cue: 'top_2in_dry',
            water_quality: 'tap_ok',
            toxicity_level: 'toxic_pets',
            toxicity_notes: 'Toxic to cats and dogs',
            has_drainage: true,
            placement_warning: null,
            calibration_factor: 1.0,
          },
        ],
      },
      all: [
        {
          plant_id: 'plant-1',
          nickname: 'Monty',
          common_name: 'Monstera',
          scientific_name: 'Monstera deliciosa',
          category: 'houseplant',
          location_name: 'Living Room',
          location_id: 'loc-1',
          assigned_to: null,
          action: 'water',
          next_due_at: '2026-11-19T10:00:00Z',
          overdue: false,
          care_cue: 'Top-drain · top 2 in dry · tap water',
          watering_method: 'top_drain',
          dryness_cue: 'top_2in_dry',
          water_quality: 'tap_ok',
          toxicity_level: 'toxic_pets',
          toxicity_notes: 'Toxic to cats and dogs',
          has_drainage: true,
          placement_warning: null,
          calibration_factor: 1.0,
        },
      ],
    });
    vi.mocked(api.gardenListPlants).mockResolvedValue([]);
    vi.mocked(api.gardenListLocations).mockResolvedValue([
      {
        id: 'loc-1',
        name: 'Living Room',
        kind: 'indoor_room',
        area_id: null,
        width_ft: null,
        length_ft: null,
        area_sqft: null,
        sun_exposure: null,
        covered: false,
        map_x: null,
        map_y: null,
        rotation_deg: null,
        light_notes: null,
        draft_notes: null,
        assigned_to: null,
        skylight_sync_enabled: false,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ]);
    vi.mocked(api.gardenGetProfile).mockResolvedValue({
      user_id: 'household',
      family_size: 7,
      hardiness_zone: '5b',
      zone_source: 'manual',
      last_spring_frost_mmdd: '05-15',
      first_fall_frost_mmdd: '10-15',
      units: 'imperial',
      preservation_prefs: null,
      daily_digest_time: '08:00',
      yard_canvas_json: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });
    vi.mocked(api.gardenStreaks).mockResolvedValue({
      user_id: 'mother',
      current_streak: 0,
      longest_streak: 0,
      total_logs: 0,
    });
  });

  afterEach(() => vi.clearAllMocks());

  it('renders due tasks with Done and Still damp buttons', async () => {
    render(<Garden />, { wrapper });
    await screen.findByText('Monty');
    expect(screen.getByText('Done')).toBeInTheDocument();
    expect(screen.getByText('Still damp')).toBeInTheDocument();
  });

  it('calls gardenLogCare when Done is clicked', async () => {
    const user = userEvent.setup();
    vi.mocked(api.gardenLogCare).mockResolvedValue({
      ok: true,
      action: 'water',
      plant_id: 'plant-1',
      logged_by: 'test',
      next_due_at: '2026-11-20T10:00:00Z',
    });
    render(<Garden />, { wrapper });
    await screen.findByText('Done');
    await user.click(screen.getByText('Done'));
    expect(vi.mocked(api.gardenLogCare)).toHaveBeenCalledWith(
      'plant-1',
      { action: 'water' },
    );
  });

  it('calls gardenSnooze when Still damp is clicked', async () => {
    const user = userEvent.setup();
    vi.mocked(api.gardenSnooze).mockResolvedValue({
      ok: true,
      plant_id: 'plant-1',
      still_damp: true,
      postpone_days: 2,
      calibration_factor: 1.1,
      next_due_at: '2026-11-21T10:00:00Z',
    });
    render(<Garden />, { wrapper });
    await screen.findByText('Still damp');
    await user.click(screen.getByText('Still damp'));
    expect(vi.mocked(api.gardenSnooze)).toHaveBeenCalledWith(
      'plant-1',
      { still_damp: true },
    );
  });

  it('calls gardenWaterAll when Water room is clicked', async () => {
    const user = userEvent.setup();
    vi.mocked(api.gardenWaterAll).mockResolvedValue({
      ok: true,
      location_id: 'loc-1',
      location_name: 'Living Room',
      watered_count: 1,
      watered: [],
    });
    render(<Garden />, { wrapper });
    await screen.findByText('Water room');
    await user.click(screen.getByText('Water room'));
    expect(vi.mocked(api.gardenWaterAll)).toHaveBeenCalledWith('loc-1');
  });

  it('shows empty state when nothing is due', async () => {
    vi.mocked(api.gardenToday).mockResolvedValue({
      date: '2026-11-19',
      due_count: 0,
      overdue_count: 0,
      by_room: {},
      all: [],
    });
    render(<Garden />, { wrapper });
    await screen.findByText('All caught up');
  });
});

describe('Garden Plants tab', () => {
  let wrapper: ReturnType<typeof createWrapper>;

  beforeEach(() => {
    wrapper = createWrapper();
    vi.mocked(api.gardenToday).mockResolvedValue({
      date: '2026-11-19',
      due_count: 0,
      overdue_count: 0,
      by_room: {},
      all: [],
    });
    vi.mocked(api.gardenListPlants).mockResolvedValue([
      {
        id: 'plant-1',
        category: 'houseplant',
        species_ref: 'monstera_deliciosa',
        nickname: 'Monty',
        quantity: 1,
        status: 'active',
        location_id: 'loc-1',
        area_sqft_used: null,
        pot_size_in: 8,
        pot_material: 'terracotta',
        has_drainage: true,
        soil_mix: 'standard_potting',
        window_direction: 'e',
        distance_from_window: 3,
        planted_date: null,
        last_repotted_date: null,
        planting_method: null,
        expected_harvest_start: null,
        expected_harvest_end: null,
        calibration_factor: 1.0,
        last_harvested_at: null,
        regrowth_cooldown_days: null,
        assigned_to: null,
        skylight_sync_enabled: false,
        notes: null,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        species: {
          key: 'monstera_deliciosa',
          category: 'houseplant',
          family: 'Araceae',
          common_name: 'Monstera',
          scientific_name: 'Monstera deliciosa',
          watering_method: 'top_drain',
          dryness_cue: 'top_2in_dry',
          water_quality: 'tap_ok',
          light_level: 'bright_indirect',
          humidity_band: 'average',
          misting_rule: 'beneficial',
          soil_mix: 'standard_potting',
          temp_min_f: 60,
          temp_max_f: 85,
          cold_draft_sensitive: 0,
          toxicity_level: 'toxic_pets',
          toxicity_notes: 'Toxic to pets',
          common_pests: 'Spider mites',
          symptoms: 'Yellow leaves',
          grooming_propagation: 'Wipe leaves',
          fertilizing: 'Monthly',
          repot_interval_months: 24,
          description: 'A popular houseplant',
          preservation_methods: '',
          usda_acidity_class: null,
          watering_interval_days: 7,
          winter_multiplier: 1.0,
          frost_tolerance: 'hardy',
          usda_zone_min: 10,
          usda_zone_max: 12,
          days_to_maturity: null,
          tall_trellis: 0,
          culinary_aliases: null,
          pinching_bolting_notes: null,
          regrowth_cadence: null,
          herb_preservation_tips: null,
        },
      },
    ]);
    vi.mocked(api.gardenListLocations).mockResolvedValue([
      {
        id: 'loc-1',
        name: 'Living Room',
        kind: 'indoor_room',
        area_id: null,
        width_ft: null,
        length_ft: null,
        area_sqft: null,
        sun_exposure: null,
        covered: false,
        map_x: null,
        map_y: null,
        rotation_deg: null,
        light_notes: null,
        draft_notes: null,
        assigned_to: null,
        skylight_sync_enabled: false,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ]);
    vi.mocked(api.gardenGetProfile).mockResolvedValue({
      user_id: 'household',
      family_size: 7,
      hardiness_zone: '5b',
      zone_source: 'manual',
      last_spring_frost_mmdd: '05-15',
      first_fall_frost_mmdd: '10-15',
      units: 'imperial',
      preservation_prefs: null,
      daily_digest_time: '08:00',
      yard_canvas_json: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });
    vi.mocked(api.gardenStreaks).mockResolvedValue({
      user_id: 'mother',
      current_streak: 0,
      longest_streak: 0,
      total_logs: 0,
    });
  });

  afterEach(() => vi.clearAllMocks());

  it('renders plant cards with nickname and species', async () => {
    render(<Garden />, { wrapper });
    fireEvent.click(screen.getByText('Plants'));
    await screen.findByText('Monty');
    await screen.findByText('Monstera');
  });

  it('opens add plant modal', async () => {
    render(<Garden />, { wrapper });
    fireEvent.click(screen.getByText('Plants'));
    await screen.findByText('Add Plant');
    fireEvent.click(screen.getByText('Add Plant'));
    await screen.findByText('Cancel');
  });
});

describe('Garden Mine filter and streak pill', () => {
  let wrapper: ReturnType<typeof createWrapper>;

  beforeEach(() => {
    wrapper = createWrapper();
    vi.mocked(api.gardenToday).mockResolvedValue({
      date: '2026-11-19',
      due_count: 1,
      overdue_count: 0,
      by_room: {},
      all: [],
    });
    vi.mocked(api.gardenListPlants).mockResolvedValue([
      {
        id: 'plant-1',
        category: 'houseplant',
        species_ref: 'monstera_deliciosa',
        nickname: 'Monty',
        quantity: 1,
        status: 'active',
        location_id: 'loc-1',
        area_sqft_used: null,
        pot_size_in: 8,
        pot_material: 'terracotta',
        has_drainage: true,
        soil_mix: 'standard_potting',
        window_direction: 'e',
        distance_from_window: 3,
        planted_date: null,
        last_repotted_date: null,
        planting_method: null,
        expected_harvest_start: null,
        expected_harvest_end: null,
        calibration_factor: 1.0,
        last_harvested_at: null,
        regrowth_cooldown_days: null,
        assigned_to: 'mother',
        skylight_sync_enabled: false,
        notes: null,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        species: {
          key: 'monstera_deliciosa',
          category: 'houseplant',
          family: 'Araceae',
          common_name: 'Monstera',
          scientific_name: 'Monstera deliciosa',
          watering_method: 'top_drain',
          dryness_cue: 'top_2in_dry',
          water_quality: 'tap_ok',
          light_level: 'bright_indirect',
          humidity_band: 'average',
          misting_rule: 'beneficial',
          soil_mix: 'standard_potting',
          temp_min_f: 60,
          temp_max_f: 85,
          cold_draft_sensitive: 0,
          toxicity_level: 'toxic_pets',
          toxicity_notes: 'Toxic to pets',
          common_pests: 'Spider mites',
          symptoms: 'Yellow leaves',
          grooming_propagation: 'Wipe leaves',
          fertilizing: 'Monthly',
          repot_interval_months: 24,
          description: 'A popular houseplant',
          preservation_methods: '',
          usda_acidity_class: null,
          watering_interval_days: 7,
          winter_multiplier: 1.0,
          frost_tolerance: 'hardy',
          usda_zone_min: 10,
          usda_zone_max: 12,
          days_to_maturity: null,
          tall_trellis: 0,
          culinary_aliases: null,
          pinching_bolting_notes: null,
          regrowth_cadence: null,
          herb_preservation_tips: null,
        },
      },
    ]);
    vi.mocked(api.gardenListLocations).mockResolvedValue([
      {
        id: 'loc-1',
        name: 'Living Room',
        kind: 'indoor_room',
        area_id: null,
        width_ft: null,
        length_ft: null,
        area_sqft: null,
        sun_exposure: null,
        covered: false,
        map_x: null,
        map_y: null,
        rotation_deg: null,
        light_notes: null,
        draft_notes: null,
        assigned_to: null,
        skylight_sync_enabled: false,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ]);
    vi.mocked(api.gardenGetProfile).mockResolvedValue({
      user_id: 'household',
      family_size: 7,
      hardiness_zone: '5b',
      zone_source: 'manual',
      last_spring_frost_mmdd: '05-15',
      first_fall_frost_mmdd: '10-15',
      units: 'imperial',
      preservation_prefs: null,
      daily_digest_time: '08:00',
      yard_canvas_json: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });
    vi.mocked(api.gardenStreaks).mockResolvedValue({
      user_id: 'mother',
      current_streak: 5,
      longest_streak: 12,
      total_logs: 20,
    });
  });

  afterEach(() => vi.clearAllMocks());

  it('shows streak pill when current_streak > 0', async () => {
    render(<Garden />, { wrapper });
    await screen.findByText('5-day streak');
  });

  it('hides streak pill when current_streak is 0', async () => {
    vi.mocked(api.gardenStreaks).mockResolvedValue({
      user_id: 'mother',
      current_streak: 0,
      longest_streak: 0,
      total_logs: 0,
    });
    render(<Garden />, { wrapper });
    expect(screen.queryByText('5-day streak')).not.toBeInTheDocument();
  });

  it('Mine filter pill filters plants by assigned user', async () => {
    render(<Garden />, { wrapper });
    fireEvent.click(screen.getByText('Plants'));
    await screen.findByText('Monty');
    // Click Mine filter - the plant is assigned to 'mother', user should be 'mother'
    fireEvent.click(screen.getByText('Mine'));
    await screen.findByText('Monty');
  });
});

describe('Garden Digest Settings button (parent+ only)', () => {
  let wrapper: ReturnType<typeof createWrapper>;

  beforeEach(() => {
    wrapper = createWrapper();
    vi.mocked(api.gardenToday).mockResolvedValue({
      date: '2026-11-19',
      due_count: 0,
      overdue_count: 0,
      by_room: {},
      all: [],
    });
    vi.mocked(api.gardenListPlants).mockResolvedValue([]);
    vi.mocked(api.gardenListLocations).mockResolvedValue([]);
    vi.mocked(api.gardenGetProfile).mockResolvedValue({
      user_id: 'household',
      family_size: 7,
      hardiness_zone: '5b',
      zone_source: 'manual',
      last_spring_frost_mmdd: '05-15',
      first_fall_frost_mmdd: '10-15',
      units: 'imperial',
      preservation_prefs: null,
      daily_digest_time: '08:00',
      yard_canvas_json: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });
    vi.mocked(api.gardenStreaks).mockResolvedValue({
      user_id: 'mother',
      current_streak: 0,
      longest_streak: 0,
      total_logs: 0,
    });
  });

  afterEach(() => vi.clearAllMocks());

  it('shows Digest Settings button for parent user', async () => {
    render(<Garden />, { wrapper });
    // The button is rendered only when isParent is true.
    // user?.is_admin is undefined in test auth, family_role undefined → isParent false.
    // But we set daily_digest_time so we need to verify the button appears for parent.
    fireEvent.click(screen.getByText('Plants'));
    await screen.findByText('Add Plant');
    // Digest Settings button should be present for parent users
    expect(screen.queryByText('Digest Settings')).toBeInTheDocument();
  });
});


/* ========================= Weather Banner (Phase 3) ========================= */

describe('Garden Weather Banner', () => {
  let wrapper: ReturnType<typeof createWrapper>;

  const baseToday = {
    date: '2026-11-19',
    due_count: 0,
    overdue_count: 0,
    by_room: {},
    all: [],
  };

  beforeEach(() => {
    wrapper = createWrapper();
    vi.mocked(api.gardenToday).mockResolvedValue({ ...baseToday, weather: null });
    vi.mocked(api.gardenListPlants).mockResolvedValue([]);
    vi.mocked(api.gardenListLocations).mockResolvedValue([]);
    vi.mocked(api.gardenGetProfile).mockResolvedValue({
      user_id: 'household', family_size: 7, hardiness_zone: '5b', zone_source: 'manual',
      last_spring_frost_mmdd: '05-15', first_fall_frost_mmdd: '10-15', units: 'imperial',
      preservation_prefs: null, daily_digest_time: '08:00', yard_canvas_json: null,
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    });
    vi.mocked(api.gardenStreaks).mockResolvedValue({
      user_id: 'mother', current_streak: 0, longest_streak: 0, total_logs: 0,
    });
  });

  afterEach(() => vi.clearAllMocks());

  it('is hidden when weather is null (unavailable)', async () => {
    render(<Garden />, { wrapper });
    await screen.findByText('All caught up');
    expect(screen.queryByText('Rain skip')).not.toBeInTheDocument();
    expect(screen.queryByText('Frost alert')).not.toBeInTheDocument();
    expect(screen.queryByText('Heat advisory')).not.toBeInTheDocument();
    expect(screen.queryByText('High wind alert')).not.toBeInTheDocument();
  });

  it('shows rain-skip banner when rain_skipped is non-empty', async () => {
    vi.mocked(api.gardenToday).mockResolvedValue({
      ...baseToday,
      weather: {
        summary: 'Rainy, 55°',
        rain_skipped: [{ plant_id: 'p1', nickname: 'Patio Basil', rain_in: 0.5, next_due_at: '2026-11-20T10:00:00Z' }],
        frost_alerts: [],
        heat_advisory: null,
        wind_advisory: null,
      },
    });
    render(<Garden />, { wrapper });
    await screen.findByText('Rain skip — watering postponed');
    expect(screen.getByText(/Patio Basil/)).toBeInTheDocument();
  });

  it('shows frost alert banner when frost_alerts is non-empty', async () => {
    vi.mocked(api.gardenToday).mockResolvedValue({
      ...baseToday,
      weather: {
        summary: 'Cold, 30°',
        rain_skipped: [],
        frost_alerts: [{
          plant_ids: ['p1'],
          names: ['Patio Basil'],
          min_temp_f: 30,
          safe_names: ['Garden Kale'],
          advice: 'Cover or bring inside: Patio Basil; Safe as-is: Garden Kale',
        }],
        heat_advisory: null,
        wind_advisory: null,
      },
    });
    render(<Garden />, { wrapper });
    await screen.findByText('Frost alert — cover or bring inside');
    expect(screen.getByText(/Patio Basil/)).toBeInTheDocument();
    expect(screen.getByText(/Garden Kale/)).toBeInTheDocument();
  });

  it('shows heat advisory banner when heat_advisory is set', async () => {
    vi.mocked(api.gardenToday).mockResolvedValue({
      ...baseToday,
      weather: {
        summary: 'Hot, 95°',
        rain_skipped: [],
        frost_alerts: [],
        heat_advisory: { high_f: 95, heat_factor: 0.75, advice: 'Early-morning watering helps.' },
        wind_advisory: null,
      },
    });
    render(<Garden />, { wrapper });
    await screen.findByText('Heat advisory');
    expect(screen.getByText(/High 95°F forecast/)).toBeInTheDocument();
  });

  it('shows wind advisory banner when wind_advisory is set', async () => {
    vi.mocked(api.gardenToday).mockResolvedValue({
      ...baseToday,
      weather: {
        summary: 'Windy, 55°',
        rain_skipped: [],
        frost_alerts: [],
        heat_advisory: null,
        wind_advisory: { gusts_mph: 40, names: ['Trellis Tomato'], advice: 'High winds (40 mph) — secure plants.' },
      },
    });
    render(<Garden />, { wrapper });
    await screen.findByText('High wind alert');
    expect(screen.getByText(/Trellis Tomato/)).toBeInTheDocument();
  });
});


/* ========================= AddPlantModal Unknown Species Lookup (Phase 3) ========================= */

describe('Garden AddPlantModal lookup', () => {
  let wrapper: ReturnType<typeof createWrapper>;

  beforeEach(() => {
    wrapper = createWrapper();
    vi.mocked(api.gardenToday).mockResolvedValue({
      date: '2026-11-19', due_count: 0, overdue_count: 0, by_room: {}, all: [],
    });
    vi.mocked(api.gardenListPlants).mockResolvedValue([]);
    vi.mocked(api.gardenListLocations).mockResolvedValue([]);
    vi.mocked(api.gardenSearch).mockResolvedValue({
      query: '', category: null, usda_zone: null, count: 0, results: [], seed_present: true,
    });
    vi.mocked(api.gardenSpeciesLookup).mockResolvedValue({
      draft: true,
      source: 'wiki_ai_draft',
      species: {
        key: 'rare_plant_x', category: 'houseplant', common_name: 'Rare Plant X',
        scientific_name: 'Rarus plantus', watering_interval_days: 10, winter_multiplier: 1.2,
        watering_method: 'top_drain', toxicity_level: 'non_toxic',
        family: null, light_level: null, dryness_cue: null, water_quality: null,
        humidity_band: null, misting_rule: null, soil_mix: null, temp_min_f: null,
        temp_max_f: null, cold_draft_sensitive: null, toxicity_notes: null, common_pests: null,
        symptoms: null, grooming_propagation: null, fertilizing: null,
        repot_interval_months: null, description: null, preservation_methods: null,
        usda_acidity_class: null, frost_tolerance: 'tender', usda_zone_min: null,
        usda_zone_max: null, days_to_maturity: null, tall_trellis: null,
      } as GardenSpecies,
      warnings: ['Review all fields before saving.'],
    });
    vi.mocked(api.gardenSaveCustomSpecies).mockResolvedValue({
      ok: true, key: 'rare_plant_x', source: 'wiki_ai_draft', warnings: [],
    });
    vi.mocked(api.gardenGetProfile).mockResolvedValue({
      user_id: 'household', family_size: 7, hardiness_zone: '5b', zone_source: 'manual',
      last_spring_frost_mmdd: '05-15', first_fall_frost_mmdd: '10-15', units: 'imperial',
      preservation_prefs: null, daily_digest_time: '08:00', yard_canvas_json: null,
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    });
    vi.mocked(api.gardenStreaks).mockResolvedValue({
      user_id: 'mother', current_streak: 0, longest_streak: 0, total_logs: 0,
    });
  });

  afterEach(() => vi.clearAllMocks());

  it('shows "Ask Jarvis to look up" when search returns no results', async () => {
    render(<Garden />, { wrapper });
    fireEvent.click(screen.getByText('Add Plant'));
    const searchInput = screen.getByPlaceholderText('e.g. Monstera, Basil, Snake Plant');
    fireEvent.change(searchInput, { target: { value: 'Rare Plant X' } });
    await screen.findByText('Ask Jarvis to look up “Rare Plant X”');
  });

  it('shows review draft and Save & Select Species button after lookup', async () => {
    render(<Garden />, { wrapper });
    fireEvent.click(screen.getByText('Add Plant'));
    const searchInput = screen.getByPlaceholderText('e.g. Monstera, Basil, Snake Plant');
    fireEvent.change(searchInput, { target: { value: 'Rare Plant X' } });
    await screen.findByText('Ask Jarvis to look up “Rare Plant X”');
    fireEvent.click(screen.getByText('Ask Jarvis to look up “Rare Plant X”'));
    await screen.findByText('Review drafted species');
    await screen.findByText(/Rare Plant X/);
    expect(screen.getByText(/Rare Plant X/)).toBeInTheDocument();
    expect(screen.getByText('Save & Select Species')).toBeInTheDocument();
  });
});
