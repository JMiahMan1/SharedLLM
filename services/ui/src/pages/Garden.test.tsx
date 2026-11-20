import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import React from 'react';
import Garden from '../pages/Garden';
import { api } from '../services/api';
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
