import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, MemoryRouter, Route, Routes } from 'react-router-dom';
import React from 'react';
import Garden, { PlantDetail } from '../pages/Garden';
import { HomeHubBindingPanel } from '../components/garden/HomeHubBindingPanel';
import { BedGridEditor } from '../components/garden/PlannerPanels';
import { api } from '../services/api';
import { saveShoppingList } from '../lib/shoppingListStore';
import type { GardenLocation, GardenPlant, GardenPlanOut, GardenSpecies } from '../types/api';
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
    gardenGetPlant: vi.fn(),
    gardenSkylightOptions: vi.fn(),
    gardenCreateSkylightChore: vi.fn(),
    gardenDeleteSkylightChore: vi.fn(),
    getFamilyDirectory: vi.fn(),
    gardenPatchPlant: vi.fn(),
    gardenDeletePlant: vi.fn(),
    gardenAssignPlant: vi.fn(),
    gardenGetProfile: vi.fn(),
    gardenStreaks: vi.fn(),
    gardenUpdateProfile: vi.fn(),
    gardenDetectZone: vi.fn(),
    gardenListPlans: vi.fn(),
    gardenCreatePlan: vi.fn(),
    gardenPatchPlan: vi.fn(),
    gardenDeletePlan: vi.fn(),
    gardenListPlanCells: vi.fn(),
    gardenUpsertPlanCells: vi.fn(),
    gardenAutoArrange: vi.fn(),
    gardenPlanChecks: vi.fn(),
    gardenPlanTimeline: vi.fn(),
    gardenListPantryGoals: vi.fn(),
    gardenCreatePantryGoal: vi.fn(),
    gardenDeletePantryGoal: vi.fn(),
    gardenCalculatePantryGoal: vi.fn(),
    gardenPlannerSizing: vi.fn(),
    gardenCreateLocation: vi.fn(),
    gardenPatchLocation: vi.fn(),
    gardenListHarvests: vi.fn(),
    gardenLogHarvest: vi.fn(),
    gardenHarvestReady: vi.fn(),
    gardenHarvestForecast: vi.fn(),
    gardenPreservationAdvice: vi.fn(),
    gardenListPantryBatches: vi.fn(),
    gardenCreatePantryBatch: vi.fn(),
    gardenPatchPantryBatch: vi.fn(),
    gardenUsePantryBatch: vi.fn(),
    gardenDeletePantryBatch: vi.fn(),
    gardenIdentifyPhoto: vi.fn(),
    gardenDiagnosePlant: vi.fn(),
    gardenUploadPlantPhoto: vi.fn(),
    gardenListPlantPhotos: vi.fn(),
    gardenDeletePlantPhoto: vi.fn(),
    gardenPlantPhotoThumbUrl: (plantId: string, photoId: string) =>
      `/api/garden/plants/${plantId}/photos/${photoId}/thumb`,
    gardenHardwareDiscover: vi.fn(),
    gardenListBindings: vi.fn(),
    gardenSaveBinding: vi.fn(),
    gardenDeleteBinding: vi.fn(),
    gardenRunValve: vi.fn(),
    gardenStopValve: vi.fn(),
    gardenScanTag: vi.fn(),
  },
}));

// Rooms are Home Hub's areas: the Add Plant modal adds a room there, never in
// Garden, so this is the same call the Rooms page makes.
const { createArea } = vi.hoisted(() => ({ createArea: vi.fn() }));
vi.mock('../lib/homeSetup', () => ({ createArea }));
vi.mock('../lib/shoppingListStore', () => ({
  saveShoppingList: vi.fn(async () => undefined),
  readShoppingList: vi.fn(async () => ({ exists: false, text: '' })),
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

// Phase 8 default: a house with no garden hardware, so nothing hardware-shaped
// may appear in any test that does not ask for it.
beforeEach(() => {
  vi.mocked(api.gardenHardwareDiscover).mockResolvedValue({
    available: false,
    has_any_hardware: false,
    moisture_sensors: [],
    temperature_sensors: [],
    humidity_sensors: [],
    lux_sensors: [],
    valves: [],
    grow_lights: [],
    tags: [],
  });
  vi.mocked(api.gardenListBindings).mockResolvedValue({ count: 0, bindings: [] });
  vi.mocked(api.getFamilyDirectory).mockResolvedValue([]);
  vi.mocked(api.gardenSkylightOptions).mockRejectedValue(new Error('no plant'));
  vi.mocked(api.gardenListPlans).mockResolvedValue({ count: 0, plans: [] });
  vi.mocked(api.gardenListPantryGoals).mockResolvedValue({ count: 0, goals: [] });
  vi.mocked(api.gardenListSpecies).mockResolvedValue({
    category: null,
    usda_zone: null,
    count: 0,
    results: [],
    seed_present: true,
  });
  vi.mocked(api.gardenPlanChecks).mockResolvedValue({ plan_id: '', checks: [], warning_count: 0 });
  vi.mocked(api.gardenPlanTimeline).mockResolvedValue({
    plan_id: '',
    plan_name: '',
    season: 'spring_summer',
    year: 2027,
    spring_frost_mmdd: null,
    first_fall_frost_mmdd: null,
    timeline: [],
  });
  vi.mocked(api.gardenListPlanCells).mockResolvedValue({ plan_id: '', bed_id: '', cells: [] });
  // Photo journal (Phase 7) defaults: an empty journal, vision never called.
  vi.mocked(api.gardenListPlantPhotos).mockResolvedValue({ plant_id: '', count: 0, photos: [] });
  // Pantry (Phase 6) defaults: empty everywhere unless a test says otherwise.
  vi.mocked(api.gardenHarvestForecast).mockResolvedValue({
    generated_on: '2026-11-20', window_days: 28, weather_used: false,
    gdd_adjusted_count: 0, count: 0, items: [],
  });
  vi.mocked(api.gardenPreservationAdvice).mockResolvedValue({
    generated_on: '2026-11-20', crop_count: 0, plant_count: 0, crops: [], combos: [],
    prep_checklist: { horizon_days: 21, crops: [], methods: [], combos: [], count: 0, items: [], categories: [] },
    safety_sources: [],
  });
  vi.mocked(api.gardenListPantryBatches).mockResolvedValue({
    generated_on: '2026-11-20', include_empty: false, goal_id: null, count: 0,
    use_first_count: 0, batches: [], goal_progress: [],
  });
  vi.mocked(api.gardenDetectZone).mockResolvedValue({
    zone: '6b',
    zone_source: 'auto',
    last_spring_frost_mmdd: '04-15',
    first_fall_frost_mmdd: '10-15',
    season_length_days: 170,
    message: 'Zone detected',
  });
});

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

describe('Garden Planner tab (Phase 5)', () => {
  let wrapper: ReturnType<typeof createWrapper>;

  const bed: GardenLocation = {
    id: 'bed-1',
    name: 'North Bed',
    kind: 'raised_bed',
    area_id: null,
    width_ft: 2,
    length_ft: 2,
    area_sqft: 4,
    sun_exposure: 'full_sun',
    covered: false,
    map_x: 1,
    map_y: 1,
    rotation_deg: 0,
    light_notes: null,
    draft_notes: null,
    assigned_to: null,
    skylight_sync_enabled: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };

  const plan: GardenPlanOut = {
    id: 'plan-1',
    name: 'Spring 2027',
    season: 'spring_summer',
    year: 2027,
    family_size: 7,
    mode: 'fresh_only',
    status: 'draft',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };

  const speciesEntry = (key: string, commonName: string, category: string): GardenSpecies => ({
    key,
    category,
    common_name: commonName,
    scientific_name: null,
    family: null,
    light_level: null,
    watering_method: null,
    dryness_cue: null,
    water_quality: null,
    humidity_band: null,
    misting_rule: null,
    soil_mix: null,
    temp_min_f: null,
    temp_max_f: null,
    cold_draft_sensitive: null,
    toxicity_level: null,
    toxicity_notes: null,
    common_pests: null,
    symptoms: null,
    grooming_propagation: null,
    fertilizing: null,
    repot_interval_months: null,
    description: null,
    preservation_methods: null,
    usda_acidity_class: null,
    watering_interval_days: null,
    winter_multiplier: null,
    frost_tolerance: null,
    usda_zone_min: 3,
    usda_zone_max: 11,
    days_to_maturity: 75,
    tall_trellis: 1,
    culinary_aliases: null,
    pinching_bolting_notes: null,
    regrowth_cadence: null,
    herb_preservation_tips: null,
  });

  beforeEach(() => {
    wrapper = createWrapper();
    vi.mocked(api.gardenToday).mockResolvedValue({
      date: '2026-11-20', due_count: 0, overdue_count: 0, by_room: {}, all: [],
    });
    vi.mocked(api.gardenListPlants).mockResolvedValue([]);
    vi.mocked(api.gardenListLocations).mockResolvedValue([bed]);
    vi.mocked(api.gardenStreaks).mockResolvedValue({
      user_id: 'mother', current_streak: 0, longest_streak: 0, total_logs: 0,
    });
    vi.mocked(api.gardenGetProfile).mockResolvedValue({
      user_id: 'household', family_size: 7, hardiness_zone: '6b', zone_source: 'manual',
      last_spring_frost_mmdd: '04-15', first_fall_frost_mmdd: '10-15', units: 'imperial',
      preservation_prefs: null, daily_digest_time: '08:00', yard_canvas_json: null,
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    });
    vi.mocked(api.gardenListPlans).mockResolvedValue({ count: 1, plans: [plan] });
    vi.mocked(api.gardenListSpecies).mockResolvedValue({
      category: null, usda_zone: null, count: 2,
      results: [speciesEntry('tomato_roma', 'Roma Tomato', 'vegetable'), speciesEntry('basil_sweet', 'Sweet Basil', 'herb')],
      seed_present: true,
    });
    vi.mocked(api.gardenPlanTimeline).mockResolvedValue({
      plan_id: plan.id, plan_name: plan.name, season: plan.season, year: plan.year,
      spring_frost_mmdd: '04-15', first_fall_frost_mmdd: '10-15', timeline: [],
    });
  });

  afterEach(() => vi.clearAllMocks());

  const openPlanner = async () => {
    render(<Garden />, { wrapper });
    fireEvent.click(screen.getByText('Planner'));
    await screen.findByText('Household Climate');
  };

  it('renders climate, sizing, plans, yard map, and timeline sections', async () => {
    await openPlanner();
    expect(screen.getByText('USDA Zone')).toBeInTheDocument();
    expect(screen.getByText('Simple family sizing')).toBeInTheDocument();
    await screen.findByText('Spring 2027');
    expect(screen.getByText('Yard Map')).toBeInTheDocument();
    await screen.findByText('Seasonal Timeline');
    expect(await screen.findByLabelText(/open north bed bed grid/i)).toBeInTheDocument();
  });

  it('calls the auto-detect zone endpoint when Auto-detect is tapped', async () => {
    await openPlanner();
    fireEvent.click(screen.getByRole('button', { name: /auto-detect/i }));
    await waitFor(() => expect(vi.mocked(api.gardenDetectZone)).toHaveBeenCalled());
    await screen.findByText(/zone detected/i);
  });

  it('calculates simple family sizing for the selected crops', async () => {
    vi.mocked(api.gardenPlannerSizing).mockResolvedValue({
      mode: 'fresh_only', family_size: 7,
      crops: [
        { species_ref: 'tomato_roma', common_name: 'Roma Tomato', plant_count: 7, sqft: 7 },
        { species_ref: 'basil_sweet', common_name: 'Sweet Basil', plant_count: 7, sqft: 7 },
      ],
      total_sqft: 14, total_plants: 14,
    });
    await openPlanner();
    await screen.findByText('Roma Tomato');
    await waitFor(() => {
      expect((screen.getByLabelText('Roma Tomato') as HTMLInputElement).checked).toBe(true);
    });
    fireEvent.click(screen.getByRole('button', { name: /^calculate$/i }));
    await waitFor(() => expect(vi.mocked(api.gardenPlannerSizing)).toHaveBeenCalled());
    const sent = vi.mocked(api.gardenPlannerSizing).mock.calls[0][0];
    expect(sent.mode).toBe('fresh_only');
    expect(sent.family_size).toBe(7);
    expect(sent.species_refs?.split(',').sort()).toEqual(['basil_sweet', 'tomato_roma']);
    await screen.findByText(/Total plants:/);
  });

  it('creates a seasonal plan from the New Plan form', async () => {
    vi.mocked(api.gardenCreatePlan).mockResolvedValue({ ...plan, id: 'plan-2', name: 'Fall Plan' });
    await openPlanner();
    fireEvent.click(screen.getByRole('button', { name: /new plan/i }));
    fireEvent.change(screen.getByLabelText(/^Name$/i), { target: { value: 'Fall Plan' } });
    fireEvent.click(screen.getByRole('button', { name: /create plan/i }));
    await waitFor(() => expect(vi.mocked(api.gardenCreatePlan)).toHaveBeenCalledWith(expect.objectContaining({ name: 'Fall Plan' })));
  });

  it('opens the square-foot bed grid and auto-arranges crops with alerts', async () => {
    vi.mocked(api.gardenAutoArrange).mockResolvedValue({
      plan_id: plan.id, bed_id: bed.id,
      placements: [
        { col: 0, row: 1, species_ref: 'tomato_roma' },
        { col: 1, row: 0, species_ref: 'basil_sweet' },
      ],
      applied: true,
    });
    vi.mocked(api.gardenPlanChecks).mockResolvedValue({
      plan_id: plan.id, warning_count: 1,
      checks: [{ cell_id: 'c1', bed_id: bed.id, kind: 'antagonist', message: 'Antagonist adjacent: potato conflicts with tomato' }],
    });

    await openPlanner();
    fireEvent.click(await screen.findByLabelText(/open north bed bed grid/i));
    await screen.findByText(/square-foot grid/i);
    fireEvent.click(screen.getByRole('button', { name: /auto-arrange/i }));
    // Arrange what is on the grid right now (empty here), not only what was saved.
    await waitFor(() => expect(vi.mocked(api.gardenAutoArrange)).toHaveBeenCalledWith(
      plan.id, bed.id, { species_refs: [], apply: true },
    ));
    await screen.findByText(/Antagonist adjacent/);
  });

  it('shows dated milestones from the seasonal timeline', async () => {
    vi.mocked(api.gardenPlanTimeline).mockResolvedValue({
      plan_id: plan.id, plan_name: plan.name, season: plan.season, year: plan.year,
      spring_frost_mmdd: '04-15', first_fall_frost_mmdd: '10-15',
      timeline: [{
        cell_id: 'c1', common_name: 'Roma Tomato', species_ref: 'tomato_roma',
        milestones: [{ stage: 'Expected harvest', week_offset: 15, mmdd: '07-29' }],
      }],
    });
    await openPlanner();
    await screen.findByText('07-29');
    expect(screen.getByText(/Expected harvest/)).toBeInTheDocument();
  });

  it('shows live pantry-goal back-calculations', async () => {
    vi.mocked(api.gardenListPantryGoals).mockResolvedValue({
      count: 1,
      goals: [{
        id: 'goal-1', item: 'Marinara sauce', crop_refs_json: '["tomato_roma"]',
        target_qty: 40, unit: 'quarts', method: 'water_bath_canning', season_year: 2027,
        notes: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
      }],
    });
    vi.mocked(api.gardenCalculatePantryGoal).mockResolvedValue({
      item: 'Marinara sauce', method: 'water_bath_canning', unit: 'quarts', target_qty: 40,
      total_raw_lbs_needed: 100, total_plant_count: 5, total_sqft: 5,
      crops: [{ species_ref: 'tomato_roma', common_name: 'Roma Tomato', raw_lbs_needed: 70, plant_count: 4, sqft: 4 }],
    });

    await openPlanner();
    fireEvent.click(screen.getByRole('button', { name: /pantry goals/i }));
    await screen.findByText('Marinara sauce');
    await screen.findByText('100');
    expect(screen.getByText(/Raw lbs/)).toBeInTheDocument();
    expect(vi.mocked(api.gardenCalculatePantryGoal)).toHaveBeenCalledWith(expect.objectContaining({
      item: 'Marinara sauce', crop_refs: 'tomato_roma', target_qty: 40,
    }));
  });
});

describe('Garden rooms come from Home Hub', () => {
  let wrapper: ReturnType<typeof createWrapper>;

  beforeEach(() => {
    wrapper = createWrapper();
    vi.mocked(api.gardenToday).mockResolvedValue({
      date: '2026-11-19', due_count: 0, overdue_count: 0, by_room: {}, all: [],
    });
    vi.mocked(api.gardenListPlants).mockResolvedValue([]);
    vi.mocked(api.gardenListLocations).mockResolvedValue([
      {
        id: 'living_room', name: 'Living Room', kind: 'indoor_room', source: 'homehub',
        area_id: 'living_room', width_ft: null, length_ft: null, area_sqft: null,
        sun_exposure: null, covered: false, map_x: null, map_y: null, rotation_deg: null,
        light_notes: null, draft_notes: null, assigned_to: null, skylight_sync_enabled: false,
        created_at: '', updated_at: '',
      },
    ]);
    // A species is picked first; the room picker only shows once one is.
    vi.mocked(api.gardenListSpecies).mockResolvedValue({
      category: null, usda_zone: null, count: 1, seed_present: true,
      results: [{
        key: 'monstera_deliciosa', category: 'houseplant', common_name: 'Monstera',
        scientific_name: 'Monstera deliciosa', description: 'Split-leaf philodendron',
      } as GardenSpecies],
    });
    createArea.mockReset();
  });

  async function openRoomPicker() {
    render(<Garden />, { wrapper });
    fireEvent.click(screen.getByText('Plants'));
    fireEvent.click(await screen.findByText('Add Plant'));
    fireEvent.click(await screen.findByText('Monstera'));
    return screen.getByLabelText('Room');
  }

  it('says the rooms are Home Hub\u2019s and links to them', async () => {
    const room = await openRoomPicker();

    expect(screen.getByText(/Rooms come from Home Hub/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Manage rooms in Home Hub/ })).toHaveAttribute(
      'href',
      '/devices#devices-areas',
    );
    // The room list is what the hub (via Garden's /locations) returned.
    expect(screen.getByRole('option', { name: 'Living Room' })).toBeInTheDocument();
    expect(room).toBeInTheDocument();
  });

  it('adds a new room in Home Hub, not in Garden', async () => {
    createArea.mockResolvedValue({ area_id: 'sunroom', name: 'Sunroom' });
    // The hub room shows up in the room list on the next read, as it does in
    // the app (creating it refreshes Garden's /locations).
    const living: GardenLocation = {
      id: 'living_room', name: 'Living Room', kind: 'indoor_room', source: 'homehub',
      area_id: 'living_room', width_ft: null, length_ft: null, area_sqft: null,
      sun_exposure: null, covered: false, map_x: null, map_y: null, rotation_deg: null,
      light_notes: null, draft_notes: null, assigned_to: null, skylight_sync_enabled: false,
      created_at: '', updated_at: '',
    };
    vi.mocked(api.gardenListLocations).mockImplementation(async () => (
      createArea.mock.calls.length
        ? [living, { ...living, id: 'sunroom', area_id: 'sunroom', name: 'Sunroom' }]
        : [living]
    ));
    const room = await openRoomPicker();

    fireEvent.click(screen.getByTitle('Add a new room'));
    fireEvent.change(screen.getByLabelText('New room name'), { target: { value: 'Sunroom' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(() => expect(createArea).toHaveBeenCalledWith({ name: 'Sunroom' }));
    // The new area is selected for the plant being added.
    await waitFor(() => expect(room).toHaveValue('sunroom'));
    // And nothing was created through Garden's own location endpoint.
    expect(vi.mocked(api.gardenCreateLocation)).not.toHaveBeenCalled();
  });
});

describe('Adding a plant for another adult', () => {
  let wrapper: ReturnType<typeof createWrapper>;

  beforeEach(() => {
    wrapper = createWrapper();
    vi.mocked(api.gardenToday).mockResolvedValue({ date: '2026-11-19', due_count: 0, overdue_count: 0, by_room: {}, all: [] });
    vi.mocked(api.gardenListPlants).mockResolvedValue([]);
    vi.mocked(api.gardenListLocations).mockResolvedValue([]);
    vi.mocked(api.gardenListSpecies).mockResolvedValue({
      category: null, usda_zone: null, count: 1, seed_present: true,
      results: [{
        key: 'monstera_deliciosa', category: 'houseplant', common_name: 'Monstera',
        scientific_name: 'Monstera deliciosa', description: 'Split-leaf philodendron',
      } as GardenSpecies],
    });
    vi.mocked(api.getFamilyDirectory).mockResolvedValue([
      { username: 'kaleb', display_name: 'Kaleb', nextcloud_user: null, avatar: null, family_role: 'adult_child' },
    ] as never);
    vi.mocked(api.gardenCreatePlant).mockResolvedValue({} as never);
  });

  it('lets a parent give the new plant to an adult, who then cares for it', async () => {
    render(<Garden />, { wrapper });
    fireEvent.click(screen.getByText('Plants'));
    fireEvent.click(await screen.findByText('Add Plant'));
    fireEvent.click(await screen.findByText('Monstera'));

    const caregiver = await screen.findByLabelText('Caregiver');
    fireEvent.change(caregiver, { target: { value: 'kaleb' } });
    const dialog = screen.getByRole('dialog', { name: /add plant/i });
    fireEvent.click(within(dialog).getByRole('button', { name: /^add plant$/i }));

    await waitFor(() => expect(vi.mocked(api.gardenCreatePlant)).toHaveBeenCalledWith(
      expect.objectContaining({ assigned_to: 'kaleb', species_ref: 'monstera_deliciosa' }),
    ));
  });
});

describe('Garden plant detail (Skylight, edit, supplies)', () => {
  const bed: GardenLocation = {
    id: 'bed-1', name: 'North Bed', kind: 'raised_bed', source: 'garden', area_id: null,
    width_ft: 2, length_ft: 2, area_sqft: 4, sun_exposure: 'full_sun', covered: false,
    map_x: 1, map_y: 1, rotation_deg: 0, light_notes: null, draft_notes: null,
    assigned_to: null, skylight_sync_enabled: false, created_at: '', updated_at: '',
  };
  const plant = {
    id: 'plant-1', category: 'houseplant', species_ref: 'monstera_deliciosa',
    location_id: null, nickname: 'Monty', quantity: 1, area_sqft_used: null,
    pot_size_in: 6, pot_material: 'terracotta', has_drainage: true, soil_mix: null,
    window_direction: 'e', distance_from_window: 2, planted_date: null, last_repotted_date: null,
    planting_method: null, expected_harvest_start: null, expected_harvest_end: null,
    status: 'active', assigned_to: 'kaleb', skylight_sync_enabled: false,
    skylight_chore_id: null, skylight_assignee: null, next_due_at: null,
    notes: null, calibration_factor: 1, last_harvested_at: null, regrowth_cooldown_days: null,
    created_at: '', updated_at: '', species: null,
  } as unknown as GardenPlant;
  const household = [
    { username: 'michele', display_name: 'Michele', nextcloud_user: null, avatar: null, family_role: 'mother' },
    { username: 'kaleb', display_name: 'Kaleb', nextcloud_user: null, avatar: null, family_role: 'adult_child' },
    { username: 'noah', display_name: 'Noah', nextcloud_user: null, avatar: null, family_role: 'child' },
  ];
  let wrapper: React.ComponentType<{ children?: React.ReactNode }>;

  beforeEach(() => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // The route element draws PlantDetail itself, so the wrapper ignores children.
    wrapper = () => (
      <MemoryRouter initialEntries={['/garden/plants/plant-1']}>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <Routes>
              <Route path="/garden/plants/:id" element={<PlantDetail />} />
            </Routes>
          </AuthProvider>
        </QueryClientProvider>
      </MemoryRouter>
    );
    vi.mocked(api.gardenGetPlant).mockResolvedValue(plant);
    vi.mocked(api.getFamilyDirectory).mockResolvedValue(household as never);
    vi.mocked(api.gardenGetPlantLogs).mockResolvedValue({ plant_id: plant.id, count: 0, logs: [] });
    vi.mocked(api.gardenListHarvests).mockResolvedValue({ harvests: [], count: 0 } as never);
    vi.mocked(api.gardenListLocations).mockResolvedValue([bed]);
    vi.mocked(api.gardenSkylightOptions).mockResolvedValue({
      can_assign_skylight: true,
      skylight_configured: true,
      eligible_assignees: [
        { username: 'kaleb', display_name: 'Kaleb', family_role: 'adult_child', skylight_name: 'Kaleb', is_self: true },
        { username: 'noah', display_name: 'Noah', family_role: 'child', skylight_name: 'Noah', is_self: false },
      ],
      current_chore_id: null,
      current_assignee: null,
      skylight_sync_enabled: false,
    });
    vi.mocked(api.gardenCreateSkylightChore).mockResolvedValue({
      plant: { ...plant, skylight_sync_enabled: true, skylight_assignee: 'noah' },
      chore: { id: '52409380', assignee: 'noah', skylight_name: 'Noah', repeat: 'weekly', title: 'Water Monty' },
    });
    vi.mocked(api.gardenPatchPlant).mockResolvedValue(plant);
  });

  it('offers the real household in the care assignment picker', async () => {
    render(<PlantDetail />, { wrapper });

    const picker = await screen.findByLabelText('Assigned to');
    const names = Array.from((picker as HTMLSelectElement).options).map((o) => o.textContent);
    expect(names).toContain('Kaleb (adult_child)');
    expect(names).toContain('Michele (mother)');
  });

  it('creates the Skylight chore for the chosen person', async () => {
    render(<PlantDetail />, { wrapper });

    fireEvent.change(await screen.findByLabelText('Assign Skylight chore to'), { target: { value: 'noah' } });
    fireEvent.click(screen.getByRole('button', { name: /assign as skylight chore/i }));

    await waitFor(() => expect(vi.mocked(api.gardenCreateSkylightChore)).toHaveBeenCalledWith(
      'plant-1', expect.objectContaining({ assignee_username: 'noah' }),
    ));
  });

  it('saves the edit form', async () => {
    render(<PlantDetail />, { wrapper });

    fireEvent.click(await screen.findByRole('button', { name: /edit plant/i }));
    fireEvent.change(screen.getByLabelText('Personal name'), { target: { value: 'Sir Monty' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(vi.mocked(api.gardenPatchPlant)).toHaveBeenCalledWith(
      'plant-1', expect.objectContaining({ nickname: 'Sir Monty' }),
    ));
  });

  it('pre-ticks only the likely supplies and adds the ticked ones', async () => {
    render(<PlantDetail />, { wrapper });

    fireEvent.click(await screen.findByRole('button', { name: /add supplies to shopping list/i }));
    const dialog = await screen.findByRole('dialog', { name: /add supplies/i });
    expect(within(dialog).getByLabelText('Potting mix')).toBeChecked();
    expect(within(dialog).getByLabelText('Compost')).not.toBeChecked();
    fireEvent.click(within(dialog).getByLabelText('Compost'));
    fireEvent.click(within(dialog).getByRole('button', { name: /add \d+ items?/i }));

    await waitFor(() => expect(saveShoppingList).toHaveBeenCalled());
    const written = String(vi.mocked(saveShoppingList).mock.calls[0][0]);
    expect(written).toContain('Potting mix');
    expect(written).toContain('Compost');
  });
});

describe('Garden Pantry tab (Phase 6)', () => {
  const forecast = {
    generated_on: '2026-11-20', window_days: 28, weather_used: true, gdd_adjusted_count: 1, count: 1,
    items: [{
      plant_id: 'plant-tomato', nickname: 'Roma Row', common_name: 'Roma (Plum) Tomato',
      category: 'vegetable', species_ref: 'tomato_roma', planted_date: '2026-06-01',
      estimated_ready_date: '2026-11-24', harvest_window_end: '2026-12-24', days_until_ready: 4,
      ready_now: false, gdd_adjusted: true, gdd_base_f: 50, gdd_daily_rate: 12.5, gdd_shift_days: -3,
      days_to_maturity: 75, harvest_window_days: 30, culinary_aliases: ['tomato'],
      preservation_methods: ['water_bath_canning'], usda_acidity_class: 'high_acid' as const,
      blanching_minutes: null, location_id: null, assigned_to: null,
    }],
  };
  const beanSafety = {
    level: 'pressure_canner_required' as const,
    title: 'Low-acid — Pressure Canner Required',
    message: 'Pressure canning is required for plain low-acid vegetables. Never water-bath can unacidified low-acid food.',
    source: 'USDA Complete Guide to Home Canning / NCHFP / Ball Blue Book',
  };
  const advice = {
    generated_on: '2026-11-20', crop_count: 1, plant_count: 2,
    crops: [{
      species_ref: 'green_bean_bush', common_name: 'Bush Green Bean', category: 'vegetable',
      culinary_aliases: ['green bean'], usda_acidity_class: 'low_acid' as const, safety: beanSafety,
      blanching_minutes: 3, herb_preservation_tips: null, preservation_conversion: null,
      plant_ids: ['p1', 'p2'], plant_count: 2,
      methods: [{
        method: 'freezing', recorded_as: 'freezing', label: 'Freezing', storage: 'Chest freezer',
        use_by_months: 10, blanching_minutes: 3, requires_acidification: false,
        supplies: ['Freezer bags or containers'],
      }],
    }],
    combos: [{
      key: 'marinara', label: 'Marinara sauce', method: 'water_bath_canning',
      method_label: 'Water-bath canning', note: 'Acidify with lemon juice.', required: ['tomato'],
      optional: ['basil'], matched_crops: ['Roma (Plum) Tomato'], missing_crops: [],
      optional_matched: [], optional_missing: ['basil'], ready: true, matched_count: 1, required_count: 1,
      safety: { level: 'water_bath_safe' as const, title: 'High-acid — Water-Bath Safe', message: 'Safe.', source: 'USDA' },
      supplies: ['Quart canning jars'],
    }],
    prep_checklist: {
      horizon_days: 21, crops: ['Bush Green Bean'],
      methods: [{ method: 'freezing', label: 'Freezing' }], combos: ['Marinara sauce'],
      count: 2, items: ['Freezer bags or containers', 'Quart canning jars'],
      categories: [{ key: 'freezer', label: 'Freezer', items: ['Freezer bags or containers'] }],
    },
    safety_sources: ['USDA Complete Guide to Home Canning / NCHFP / Ball Blue Book'],
  };
  const pantry = {
    generated_on: '2026-11-20', include_empty: false, goal_id: null, count: 1, use_first_count: 1,
    batches: [{
      id: 'batch-1', goal_id: 'goal-1', label: 'Marinara sauce', method: 'water_bath_canning',
      method_label: 'Water-bath canning', batch_date: '2026-09-15', qty_initial: 12, qty_remaining: 8,
      unit: 'quarts', storage_location: 'Basement shelf', use_by_date: '2027-12-15',
      logged_by: 'mother', notes: null, created_at: '2026-09-15T00:00:00Z',
      updated_at: '2026-09-15T00:00:00Z', days_until_use_by: 30, past_use_by: false,
      use_first: true, oldest_in_label: true,
    }],
    goal_progress: [{
      goal_id: 'goal-1', item: 'Marinara sauce', unit: 'quarts', target_qty: 40, qty_put_up: 28,
      qty_remaining: 8, percent_complete: 70, batch_count: 1, unit_mismatch: false,
      mismatched_units: [], season_year: 2026,
    }],
  };

  const openPantry = async () => {
    const wrapper = createWrapper();
    render(<Garden />, { wrapper });
    fireEvent.click(screen.getByRole('button', { name: 'Pantry' }));
    await screen.findByRole('heading', { name: 'Coming Ready Soon' });
  };

  beforeEach(() => {
    vi.mocked(api.gardenToday).mockResolvedValue({ date: '2026-11-19', due_count: 0, overdue_count: 0, by_room: {}, all: [] });
    vi.mocked(api.gardenListPlants).mockResolvedValue([]);
    vi.mocked(api.gardenListLocations).mockResolvedValue([]);
    vi.mocked(api.gardenHarvestForecast).mockResolvedValue(forecast);
    vi.mocked(api.gardenPreservationAdvice).mockResolvedValue(advice);
    vi.mocked(api.gardenListPantryBatches).mockResolvedValue(pantry);
    vi.mocked(api.gardenUsePantryBatch).mockResolvedValue({ ...pantry.batches[0], qty_remaining: 7 });
    vi.mocked(api.gardenCreatePantryBatch).mockResolvedValue({} as never);
  });

  it('shows what is coming ready soon with its GDD badge', async () => {
    await openPantry();

    expect(await screen.findByText('Roma Row')).toBeInTheDocument();
    expect(screen.getByText('Ready in 4 days')).toBeInTheDocument();
    expect(screen.getByText('GDD')).toBeInTheDocument();
  });

  it('warns that low-acid crops need a pressure canner', async () => {
    await openPantry();

    expect(await screen.findByText('Low-acid — Pressure Canner Required')).toBeInTheDocument();
    expect(screen.getByText(/Never water-bath can unacidified low-acid food/)).toBeInTheDocument();
  });

  it('suggests a combo the garden can actually make', async () => {
    await openPantry();

    expect(await screen.findByText('You have everything')).toBeInTheDocument();
    expect(screen.getAllByText(/Roma \(Plum\) Tomato/).length).toBeGreaterThan(0);
  });

  it('tracks the goal progress bar at 28 of 40 quarts', async () => {
    await openPantry();

    const bar = await screen.findByRole('progressbar', { name: 'Marinara sauce progress' });
    expect(bar).toHaveAttribute('aria-valuenow', '70');
    expect(screen.getByText('28 / 40 quarts put up (70%)')).toBeInTheDocument();
  });

  it('opens the prep-supplies picker with the forecast supplies', async () => {
    await openPantry();

    fireEvent.click(await screen.findByRole('button', { name: /Prep supplies/ }));

    const dialog = await screen.findByRole('dialog', { name: /shopping list/i });
    expect(within(dialog).getByText('Quart canning jars')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: /Add 2 items/ }));
    await waitFor(() => expect(vi.mocked(saveShoppingList)).toHaveBeenCalled());
  });

  it('lets an adult log a preserved batch', async () => {
    await openPantry();

    fireEvent.click(await screen.findByRole('button', { name: 'Log Batch' }));
    const dialog = await screen.findByRole('dialog', { name: /Log preserved batch/i });
    fireEvent.change(within(dialog).getByLabelText(/What did you put up/), { target: { value: 'Dill pickles' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add batch' }));

    await waitFor(() => expect(vi.mocked(api.gardenCreatePantryBatch)).toHaveBeenCalledWith(
      expect.objectContaining({ label: 'Dill pickles', method: 'water_bath_canning', qty_initial: 12, unit: 'quarts' }),
    ));
  });

  it('takes one jar out with -1 Used', async () => {
    await openPantry();

    const useFirst = await screen.findByText('Use first: Marinara sauce');
    expect(useFirst).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: '−1 Used' }));

    await waitFor(() => expect(vi.mocked(api.gardenUsePantryBatch)).toHaveBeenCalledWith('batch-1', 1));
  });
});

describe('Garden photos, snap-to-add and the Plant Doctor (Phase 7)', () => {
  const plant = {
    id: 'plant-1', category: 'houseplant', species_ref: 'monstera_deliciosa',
    location_id: null, nickname: 'Monty', quantity: 1, area_sqft_used: null,
    pot_size_in: 6, pot_material: 'terracotta', has_drainage: true, soil_mix: null,
    window_direction: 'e', distance_from_window: 2, planted_date: null, last_repotted_date: null,
    planting_method: null, expected_harvest_start: null, expected_harvest_end: null,
    status: 'active', assigned_to: 'kaleb', skylight_sync_enabled: false,
    skylight_chore_id: null, skylight_assignee: null, next_due_at: null,
    notes: null, calibration_factor: 1, last_harvested_at: null, regrowth_cooldown_days: null,
    created_at: '', updated_at: '', species: null,
  } as unknown as GardenPlant;

  const identification = {
    status: 'ok',
    identified: {
      common_name: 'Monstera', scientific_name: 'Monstera deliciosa', category: 'houseplant',
      confidence: 'high', is_seed_packet: false, notes: 'Split leaves', description: 'A climber.',
    },
    matches: [{
      key: 'monstera_deliciosa', category: 'houseplant', common_name: 'Monstera (Swiss Cheese Plant)',
      scientific_name: 'Monstera deliciosa', description: 'Fenestrated climber.',
    }],
    in_catalog: true,
    can_draft_profile: false,
    uncertain: [],
  };

  const diagnosis = {
    likely_causes: [{ cause: 'overwatering', confidence: 'high', why: 'yellow lower leaves' }],
    confidence: 'high',
    urgency: 'medium',
    recovery_steps: ['Let the top 2 inches dry out', 'Check the drainage holes'],
    care_adjustments: ['Water every 10 days instead of 7'],
    summary: 'Too much water. Let it dry out.',
  };

  const journalPhoto = {
    id: 'photo-1', plant_id: 'plant-1', nextcloud_path: 'Garden/Photos/plant-1/a.jpg',
    thumb_path: 'photo-1.jpg', taken_at: '2026-11-18T09:00:00+00:00', taken_by: 'kaleb',
    kind: 'progress', ai_summary_json: null, notes: 'New leaf', created_at: '2026-11-18T09:00:00+00:00',
  };

  const leaf = () => new File([new Uint8Array([0xff, 0xd8, 0xff])], 'leaf.jpg', { type: 'image/jpeg' });

  it('identifies a plant from a photo and fills the form from the catalog', async () => {
    vi.mocked(api.gardenToday).mockResolvedValue({ date: '2026-11-19', due_count: 0, overdue_count: 0, by_room: {}, all: [] });
    vi.mocked(api.gardenListPlants).mockResolvedValue([]);
    vi.mocked(api.gardenListLocations).mockResolvedValue([]);
    vi.mocked(api.gardenListSpecies).mockResolvedValue({ category: null, usda_zone: null, count: 0, results: [], seed_present: true });
    vi.mocked(api.gardenIdentifyPhoto).mockResolvedValue(identification as never);

    render(<Garden />, { wrapper: createWrapper() });
    fireEvent.click(screen.getByText('Plants'));
    fireEvent.click(await screen.findByText('Add Plant'));

    fireEvent.change(document.getElementById('garden-identify-photo') as HTMLInputElement, {
      target: { files: [leaf()] },
    });

    expect(await screen.findByText('Monstera')).toBeInTheDocument();
    expect(screen.getByText('high confidence')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Use Monstera \(Swiss Cheese Plant\)/ }));

    // Choosing the match selects the species, so the rest of the form is next.
    await waitFor(() => expect(vi.mocked(api.gardenIdentifyPhoto)).toHaveBeenCalled());
    expect(await screen.findByText('Monstera deliciosa')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Change' })).toBeInTheDocument();
  });

  it('offers a draft profile when the photo is not in the catalog', async () => {
    vi.mocked(api.gardenToday).mockResolvedValue({ date: '2026-11-19', due_count: 0, overdue_count: 0, by_room: {}, all: [] });
    vi.mocked(api.gardenListPlants).mockResolvedValue([]);
    vi.mocked(api.gardenListLocations).mockResolvedValue([]);
    vi.mocked(api.gardenListSpecies).mockResolvedValue({ category: null, usda_zone: null, count: 0, results: [], seed_present: true });
    vi.mocked(api.gardenIdentifyPhoto).mockResolvedValue({
      ...identification,
      matches: [],
      in_catalog: false,
      can_draft_profile: true,
      identified: { ...identification.identified, common_name: 'Philodendron spiritus-sancti' },
    } as never);

    render(<Garden />, { wrapper: createWrapper() });
    fireEvent.click(screen.getByText('Plants'));
    fireEvent.click(await screen.findByText('Add Plant'));
    fireEvent.change(document.getElementById('garden-identify-photo') as HTMLInputElement, {
      target: { files: [leaf()] },
    });

    expect(await screen.findByText(/Not in the catalog yet/i)).toBeInTheDocument();
  });

  it('diagnoses a plant from a leaf photo and keeps the diagnosis', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.mocked(api.gardenGetPlant).mockResolvedValue(plant);
    vi.mocked(api.gardenGetPlantLogs).mockResolvedValue({ plant_id: plant.id, count: 0, logs: [] });
    vi.mocked(api.gardenListHarvests).mockResolvedValue({ harvests: [], count: 0 } as never);
    vi.mocked(api.gardenListLocations).mockResolvedValue([]);
    vi.mocked(api.getFamilyDirectory).mockResolvedValue([]);
    vi.mocked(api.gardenDiagnosePlant).mockResolvedValue({
      diagnosis,
      photo: { ...journalPhoto, kind: 'diagnosis' },
      nextcloud_warning: null,
    });

    render(
      <MemoryRouter initialEntries={['/garden/plants/plant-1']}>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <Routes>
              <Route path="/garden/plants/:id" element={<PlantDetail />} />
            </Routes>
          </AuthProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Plant Doctor/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Plant Doctor' });
    fireEvent.change(within(dialog).getByLabelText(/What have you noticed/), {
      target: { value: 'yellow lower leaves' },
    });
    fireEvent.change(document.getElementById('doctor-photo') as HTMLInputElement, {
      target: { files: [leaf()] },
    });

    expect(await within(dialog).findByText('Too much water. Let it dry out.')).toBeInTheDocument();
    expect(within(dialog).getByText(/MEDIUM urgency/)).toBeInTheDocument();
    expect(within(dialog).getByText('Let the top 2 inches dry out')).toBeInTheDocument();
    await waitFor(() =>
      expect(vi.mocked(api.gardenDiagnosePlant)).toHaveBeenCalledWith(
        'plant-1',
        expect.any(File),
        'yellow lower leaves',
      ),
    );
  });

  it('shows the growth journal with thumbnails and can delete a photo', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.mocked(api.gardenGetPlant).mockResolvedValue(plant);
    vi.mocked(api.gardenGetPlantLogs).mockResolvedValue({ plant_id: plant.id, count: 0, logs: [] });
    vi.mocked(api.gardenListHarvests).mockResolvedValue({ harvests: [], count: 0 } as never);
    vi.mocked(api.gardenListLocations).mockResolvedValue([]);
    vi.mocked(api.getFamilyDirectory).mockResolvedValue([]);
    vi.mocked(api.gardenListPlantPhotos).mockResolvedValue({
      plant_id: plant.id, count: 1, photos: [journalPhoto],
    });
    vi.mocked(api.gardenDeletePlantPhoto).mockResolvedValue(undefined);

    render(
      <MemoryRouter initialEntries={['/garden/plants/plant-1']}>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <Routes>
              <Route path="/garden/plants/:id" element={<PlantDetail />} />
            </Routes>
          </AuthProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText('Growth Journal')).toBeInTheDocument();
    const thumb = await screen.findByAltText(/progress photo from 2026-11-18/);
    expect(thumb).toHaveAttribute('src', '/api/garden/plants/plant-1/photos/photo-1/thumb');
    expect(screen.getByText('New leaf')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Delete photo from 2026-11-18/ }));
    await waitFor(() => expect(vi.mocked(api.gardenDeletePlantPhoto)).toHaveBeenCalledWith('plant-1', 'photo-1'));
  });
});

describe('Garden Home Hub hardware (Phase 8)', () => {
  const plant = {
    id: 'plant-1', category: 'vegetable', species_ref: 'tomato_roma',
    location_id: 'bed-1', nickname: 'Roma Row', quantity: 1, area_sqft_used: null,
    pot_size_in: null, pot_material: null, has_drainage: true, soil_mix: null,
    window_direction: null, distance_from_window: null, planted_date: null,
    last_repotted_date: null, planting_method: null, expected_harvest_start: null,
    expected_harvest_end: null, status: 'active', assigned_to: null,
    skylight_sync_enabled: false, skylight_chore_id: null, skylight_assignee: null,
    next_due_at: '2026-11-22T09:00:00Z', notes: null, calibration_factor: 1,
    last_harvested_at: null, regrowth_cooldown_days: null, created_at: '', updated_at: '',
    species: null, live_moisture_pct: 22,
  } as unknown as GardenPlant;

  const binding = {
    id: 'binding-1', target_kind: 'plant' as const, target_id: 'plant-1', target_name: 'Roma Row',
    moisture_entity_id: 'sensor.bed1_soil_moisture', temperature_entity_id: null,
    humidity_entity_id: null, lux_entity_id: null, grow_light_entity_id: null,
    grow_light_on_time: null, grow_light_off_time: null, grow_light_enabled: false,
    valve_entity_id: 'valve.bed1_drip', valve_mode: 'notify_only' as const,
    moisture_override_enabled: true, moisture_threshold_pct: 30,
    max_run_minutes: 5, daily_max_runs: 2, rain_interlock: true,
    active_run_started_at: null, active_run_ends_at: null, nfc_tag_id: null,
    readings: { moisture_pct: 22, bound: true },
    runs_today: 1, created_at: '', updated_at: '',
  };

  const discovery = {
    available: true, has_any_hardware: true,
    moisture_sensors: [{ entity_id: 'sensor.bed1_soil_moisture', friendly_name: 'Bed 1 Soil Moisture', device_class: 'moisture', unit: '%', state: '22' }],
    temperature_sensors: [], humidity_sensors: [], lux_sensors: [],
    valves: [{ entity_id: 'valve.bed1_drip', friendly_name: 'Bed 1 Drip', device_class: null, unit: null, state: 'closed' }],
    grow_lights: [], tags: [],
  };

  const renderDetail = () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.mocked(api.gardenGetPlant).mockResolvedValue(plant);
    vi.mocked(api.gardenGetPlantLogs).mockResolvedValue({ plant_id: plant.id, count: 0, logs: [] });
    vi.mocked(api.gardenListHarvests).mockResolvedValue({ harvests: [], count: 0 } as never);
    vi.mocked(api.gardenListLocations).mockResolvedValue([]);
    vi.mocked(api.getFamilyDirectory).mockResolvedValue([]);
    return render(
      <MemoryRouter initialEntries={['/garden/plants/plant-1']}>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <Routes>
              <Route path="/garden/plants/:id" element={<PlantDetail />} />
            </Routes>
          </AuthProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    );
  };

  it('shows no hardware interface at all in a house without any', async () => {
    renderDetail();

    expect(await screen.findByText('Growth Journal')).toBeInTheDocument();
    expect(screen.queryByText(/Home Hub Hardware/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Run valve/i)).not.toBeInTheDocument();
  });

  it('shows live readings and the guarded valve controls when hardware exists', async () => {
    vi.mocked(api.gardenHardwareDiscover).mockResolvedValue(discovery);
    vi.mocked(api.gardenListBindings).mockResolvedValue({ count: 1, bindings: [binding] });
    renderDetail();

    expect(await screen.findByText(/Home Hub Hardware/)).toBeInTheDocument();
    expect(screen.getByText('22%')).toBeInTheDocument();
    expect(screen.getByText(/1 \/ 2 runs today/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Run valve/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Stop now/i })).toBeInTheDocument();
    // The safety caps are visible, not hidden behind a tooltip.
    expect(screen.getByLabelText(/Max run minutes/i)).toBeInTheDocument();
  });

  it('does not offer the valve or the editor to someone who may not use it', async () => {
    vi.mocked(api.gardenHardwareDiscover).mockResolvedValue(discovery);
    vi.mocked(api.gardenListBindings).mockResolvedValue({ count: 1, bindings: [binding] });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <HomeHubBindingPanel targetKind="plant" targetId="plant-1" canEdit={false} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText(/Home Hub Hardware/)).toBeInTheDocument();
    expect(screen.getByText('22%')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Run valve/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/Edit hardware/i)).not.toBeInTheDocument();
  });
});

describe('Garden NFC/QR tag cards (Phase 8)', () => {
  afterEach(() => {
    window.history.pushState({}, '', '/');
  });

  const bedScan = {
    matched: true,
    tag_id: 'tag-bed1',
    target_kind: 'location' as const,
    target_id: 'bed-1',
    target_name: 'Bed 1',
    plant: null,
    location: { id: 'bed-1', name: 'Bed 1', kind: 'raised_bed', plant_count: 3 },
    readings: { moisture_pct: 22 },
    moisture: { applies: true, due: true, reason: 'Soil moisture is 22% (below 30%)' },
    next_due_at: null,
    due_today: true,
    actions: ['water', 'photo'],
  };

  it('opens the card for a scanned tag and waters that bed in one tap', async () => {
    window.history.pushState({}, '', '/garden?tag=tag-bed1');
    vi.mocked(api.gardenToday).mockResolvedValue({ date: '2026-11-20', due_count: 0, overdue_count: 0, by_room: {}, all: [] });
    vi.mocked(api.gardenListPlants).mockResolvedValue([]);
    vi.mocked(api.gardenListLocations).mockResolvedValue([]);
    vi.mocked(api.gardenScanTag).mockResolvedValue(bedScan);
    vi.mocked(api.gardenWaterAll).mockResolvedValue({
      ok: true, location_id: 'bed-1', location_name: 'Bed 1', watered_count: 3, watered: [],
    });

    render(<Garden />, { wrapper: createWrapper() });

    expect(await screen.findByText('Scanned tag')).toBeInTheDocument();
    expect(screen.getByText('Bed 1')).toBeInTheDocument();
    expect(screen.getByText(/3 plants in this bed/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Water this bed/i }));

    // The Garden records that a scanned tag did this, not the room button.
    await waitFor(() =>
      expect(vi.mocked(api.gardenWaterAll)).toHaveBeenCalledWith('bed-1', { source: 'nfc_tag' }),
    );
  });

  it('says plainly when a tag is not linked yet', async () => {
    window.history.pushState({}, '', '/garden?tag=tag-unknown');
    vi.mocked(api.gardenToday).mockResolvedValue({ date: '2026-11-20', due_count: 0, overdue_count: 0, by_room: {}, all: [] });
    vi.mocked(api.gardenListPlants).mockResolvedValue([]);
    vi.mocked(api.gardenListLocations).mockResolvedValue([]);
    vi.mocked(api.gardenScanTag).mockRejectedValue(new Error('not linked'));

    render(<Garden />, { wrapper: createWrapper() });

    expect(await screen.findByText(/not linked to a plant or bed yet/i)).toBeInTheDocument();
  });
});

describe('Home Hub hardware edits (Phase 8)', () => {
  const binding = {
    id: 'binding-1', target_kind: 'plant' as const, target_id: 'plant-1', target_name: 'Roma Row',
    moisture_entity_id: 'sensor.bed1_soil_moisture', temperature_entity_id: null,
    humidity_entity_id: null, lux_entity_id: null,
    grow_light_entity_id: 'light.grow_shelf', grow_light_on_time: null, grow_light_off_time: null,
    grow_light_enabled: false, valve_entity_id: null, valve_mode: 'notify_only' as const,
    moisture_override_enabled: false, moisture_threshold_pct: 30,
    max_run_minutes: 5, daily_max_runs: 2, rain_interlock: true,
    active_run_started_at: null, active_run_ends_at: null, nfc_tag_id: null,
    readings: { moisture_pct: 22 }, runs_today: 0, created_at: '', updated_at: '',
  };

  const discovery = {
    available: true, has_any_hardware: true,
    moisture_sensors: [], temperature_sensors: [], humidity_sensors: [], lux_sensors: [], valves: [],
    grow_lights: [{ entity_id: 'light.grow_shelf', friendly_name: 'Grow Shelf', device_class: null, unit: null, state: 'off' }],
    tags: [],
  };

  const renderPanel = () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.mocked(api.gardenHardwareDiscover).mockResolvedValue(discovery);
    vi.mocked(api.gardenListBindings).mockResolvedValue({ count: 1, bindings: [binding] });
    return render(
      <QueryClientProvider client={queryClient}>
        <HomeHubBindingPanel targetKind="plant" targetId="plant-1" canEdit />
      </QueryClientProvider>,
    );
  };

  it('saves the grow-light times the inputs are showing', async () => {
    vi.mocked(api.gardenSaveBinding).mockImplementation(async (_kind, _id, body) => ({ ...binding, ...body } as never));
    renderPanel();

    fireEvent.click(await screen.findByText(/Edit hardware/i));
    fireEvent.click(screen.getByLabelText(/Grow light schedule/i));
    fireEvent.click(screen.getByRole('button', { name: /Save hardware/i }));

    await waitFor(() => expect(vi.mocked(api.gardenSaveBinding)).toHaveBeenCalled());
    const [, , body] = vi.mocked(api.gardenSaveBinding).mock.calls[0];
    expect(body.grow_light_enabled).toBe(true);
    // Never a 400 for times the person could see but never touched.
    expect(body.grow_light_on_time).toBe('06:00');
    expect(body.grow_light_off_time).toBe('20:00');
  });

  it('can remove a binding again', async () => {
    vi.mocked(api.gardenDeleteBinding).mockResolvedValue(undefined);
    renderPanel();

    fireEvent.click(await screen.findByText(/Edit hardware/i));
    fireEvent.click(screen.getByRole('button', { name: /Remove hardware/i }));

    await waitFor(() =>
      expect(vi.mocked(api.gardenDeleteBinding)).toHaveBeenCalledWith('plant', 'plant-1'),
    );
  });

  it('shows a bed\'s hardware inside the bed grid for a parent', async () => {
    const bed = {
      id: 'bed-1', name: 'North Bed', kind: 'raised_bed', source: 'garden', area_id: null,
      width_ft: 2, length_ft: 2, area_sqft: 4, sun_exposure: 'full_sun', covered: false,
      map_x: 1, map_y: 1, rotation_deg: 0, light_notes: null, draft_notes: null,
      assigned_to: null, skylight_sync_enabled: false, created_at: '', updated_at: '',
    } as unknown as GardenLocation;
    vi.mocked(api.gardenHardwareDiscover).mockResolvedValue(discovery);
    vi.mocked(api.gardenListBindings).mockResolvedValue({
      count: 1,
      bindings: [{ ...binding, target_kind: 'location', target_id: 'bed-1', target_name: 'North Bed' }],
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <BedGridEditor planId="plan-1" bed={bed} species={[]} onClose={vi.fn()} canEdit />
      </QueryClientProvider>,
    );

    expect(await screen.findByText(/Home Hub Hardware/)).toBeInTheDocument();
    expect(vi.mocked(api.gardenListBindings)).toHaveBeenCalledWith({ location_id: 'bed-1' });
  });
});
