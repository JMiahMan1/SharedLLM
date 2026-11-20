import { test, expect as baseExpect, type Locator, type Page, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { openAllDetails, quietHotReload, screenFindings, settle, shot } from '../fixtures/pageAudit';

/**
 * Garden page (Today, Plants, Planner tabs, Add Plant modal, Digest Settings
 * modal, Bed Grid Editor, and Plant Detail) across all five form factors:
 * no overlapping buttons or controls, 44px touch targets on phones, no
 * hard-clipped text, nothing outside its card or the viewport, and no
 * sideways page scroll.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

/** The smallest real JPEG, so the journal's thumbnails lay out as images. */
const TINY_JPEG = Buffer.from(
  '/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a' +
    'HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA' +
    'AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==',
  'base64',
);

interface Run {
  home: HomeMocks;
  errors: string[];
  findings: string[];
}

const SAMPLE_SPECIES = [
  {
    key: 'monstera_deliciosa',
    category: 'houseplant',
    common_name: 'Monstera Deliciosa',
    scientific_name: 'Monstera deliciosa',
    watering_interval_days: 7,
    winter_multiplier: 1.5,
    watering_method: 'top_drain',
    dryness_cue: 'top_2in_dry',
    water_quality: 'tap_ok',
    light_level: 'bright_indirect',
    misting_rule: 'optional',
    toxicity_level: 'toxic_pets',
    toxicity_notes: 'Calcium oxalates irritate mouths if chewed.',
    common_pests: 'Spider mites, thrips',
    description: 'Fenestrated tropical climber.',
  },
  {
    key: 'basil_sweet',
    category: 'herb',
    common_name: 'Sweet Basil',
    scientific_name: 'Ocimum basilicum',
    watering_interval_days: 3,
    winter_multiplier: 1.3,
    watering_method: 'evenly_moist',
    dryness_cue: 'surface_damp',
    light_level: 'full_sun',
    toxicity_level: 'non_toxic',
    plants_per_sqft: 4,
    crop_family: 'lamiaceae',
    culinary_aliases: 'basil, sweet basil',
    pinching_bolting_notes: 'Pinch flower buds early to keep leaves sweet.',
    regrowth_cadence: '5-7 days',
    herb_preservation_tips: 'Freeze chopped leaves in olive oil cubes.',
    description: 'Fragrant kitchen herb.',
  },
  {
    key: 'tomato_roma',
    category: 'vegetable',
    common_name: 'Roma Tomato',
    scientific_name: 'Solanum lycopersicum',
    watering_interval_days: 3,
    winter_multiplier: 1.0,
    light_level: 'full_sun',
    toxicity_level: 'non_toxic',
    plants_per_sqft: 1,
    yield_per_plant_lbs: 10,
    tall_trellis: 1,
    crop_family: 'solanaceae',
    days_to_maturity: 75,
    description: 'Meaty paste tomato for sauce.',
  },
];

const SAMPLE_LOCATIONS = [
  { id: 'loc-living', name: 'Living Room', kind: 'indoor_room', indoor: true },
  {
    id: 'loc-bed-north',
    name: 'North Raised Bed',
    kind: 'raised_bed',
    indoor: false,
    width_ft: 4,
    length_ft: 8,
    map_x: 2,
    map_y: 2,
    rotation_deg: 0,
  },
];

const SAMPLE_PLANTS = [
  {
    id: 'plant-monty',
    category: 'houseplant',
    species_ref: 'monstera_deliciosa',
    location_id: 'loc-living',
    nickname: 'Monty',
    status: 'active',
    assigned_to: 'mother',
    skylight_sync_enabled: true,
    pot_size_in: 10,
    pot_material: 'terracotta',
    has_drainage: true,
    window_direction: 'e',
    distance_from_window: 4,
    species: SAMPLE_SPECIES[0],
  },
  {
    id: 'plant-basil',
    category: 'herb',
    species_ref: 'basil_sweet',
    location_id: 'loc-living',
    nickname: 'Sir Basil',
    status: 'active',
    assigned_to: null,
    skylight_sync_enabled: false,
    pot_size_in: 6,
    pot_material: 'ceramic',
    has_drainage: true,
    window_direction: 's',
    distance_from_window: 1,
    regrowth_cooldown_days: 6,
    species: SAMPLE_SPECIES[1],
  },
];

const SAMPLE_PLAN = {
  id: 'plan-2027',
  name: 'Spring/Summer 2027 Family Garden',
  season: 'spring_summer',
  year: 2027,
  family_size: 7,
  mode: 'fresh_plus_preserving',
  status: 'active',
  created_at: '2026-11-20T00:00:00Z',
  updated_at: '2026-11-20T00:00:00Z',
};

/**
 * Check that no two visible interactive controls (buttons, links, inputs, selects)
 * overlap each other by more than 2px in both axes.
 */
async function overlappingControls(scope: Locator, where: string): Promise<string[]> {
  return scope.evaluate((root, labelPrefix) => {
    const selector = 'button, a[href], input:not([type="hidden"]), select, textarea, [role="tab"]';
    const els = Array.from(root.querySelectorAll<HTMLElement>(selector)).filter((el) => {
      const b = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return b.width > 1 && b.height > 1 && s.visibility !== 'hidden' && s.display !== 'none' && !el.closest('.sr-only');
    });
    const describe = (el: HTMLElement) =>
      (el.getAttribute('aria-label') || el.textContent || el.getAttribute('placeholder') || el.tagName)
        .trim()
        .replace(/\s+/g, ' ')
        .slice(0, 32);

    const rects = els.map((el) => ({ el, rect: el.getBoundingClientRect(), label: describe(el) }));
    const issues: string[] = [];
    for (let i = 0; i < rects.length; i += 1) {
      for (let j = i + 1; j < rects.length; j += 1) {
        const a = rects[i];
        const b = rects[j];
        // Ignore ancestor/descendant relationships (e.g. control inside button/link).
        if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
        const ox = Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left);
        const oy = Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top);
        if (ox > 2 && oy > 2) {
          issues.push(`${labelPrefix}: overlapping controls "${a.label}" & "${b.label}" (${Math.round(ox)}x${Math.round(oy)}px)`);
        }
      }
    }
    return issues;
  }, where);
}

async function openGarden(page: Page, path = '/garden'): Promise<Run> {
  await quietHotReload(page);
  const home = await mockHome(page, 'admin');
  await page.route('**/api/garden/**', (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const method = route.request().method();
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

    if (p === '/api/garden/today') {
      return json({
        date: '2026-11-20',
        due_count: 1,
        overdue_count: 0,
        weather: {
          rain_skipped: [],
          frost_alerts: [{ plant_id: 'plant-basil', advice: 'Bring tender herbs inside tonight (31°F).' }],
          heat_advisory: null,
          wind_advisory: null,
          summary: 'Overnight low 31°F — protect tender plants.',
        },
        by_room: {
          'Living Room': [
            {
              plant_id: 'plant-monty',
              nickname: 'Monty',
              common_name: 'Monstera Deliciosa',
              action: 'water',
              care_cue: 'Top 2 in dry — water thoroughly',
              location_name: 'Living Room',
              toxicity_level: 'toxic_pets',
              assigned_to: 'mother',
              overdue: false,
              next_due_at: '2026-11-20',
            },
          ],
        },
        all: [],
      });
    }
    if (p === '/api/garden/harvest/ready') {
      return json({
        count: 1,
        ready: [
          {
            plant_id: 'plant-basil',
            nickname: 'Sir Basil',
            common_name: 'Sweet Basil',
            category: 'herb',
            species_ref: 'basil_sweet',
            culinary_aliases: ['basil', 'sweet basil'],
            in_cooldown: false,
            pinching_bolting_notes: 'Pinch flower buds early.',
            herb_preservation_tips: 'Freeze in olive oil cubes.',
          },
        ],
      });
    }
    if (p === '/api/users/family-directory') {
      return json([
        { username: 'mother', display_name: 'Michele', nextcloud_user: null, avatar: null, family_role: 'mother' },
        { username: 'father', display_name: 'Jeremiah', nextcloud_user: null, avatar: null, family_role: 'father' },
        { username: 'kaleb', display_name: 'Kaleb', nextcloud_user: null, avatar: null, family_role: 'adult_child' },
        { username: 'gracie', display_name: 'Gracie', nextcloud_user: null, avatar: null, family_role: 'teen' },
        { username: 'noah', display_name: 'Noah', nextcloud_user: null, avatar: null, family_role: 'child' },
      ]);
    }
    if (/^\/api\/garden\/plants\/[^/]+\/skylight-options$/.test(p)) {
      return json({
        can_assign_skylight: true,
        skylight_configured: true,
        eligible_assignees: [
          { username: 'mother', display_name: 'Michele', family_role: 'mother', skylight_name: 'Michele', is_self: true },
          { username: 'noah', display_name: 'Noah', family_role: 'child', skylight_name: 'Noah', is_self: false },
        ],
        current_chore_id: null,
        current_assignee: null,
        skylight_sync_enabled: false,
      });
    }
    if (p === '/api/garden/plants/plant-monty') return json(SAMPLE_PLANTS[0]);
    if (p === '/api/garden/plants') return json(SAMPLE_PLANTS);
    if (p === '/api/garden/locations') return json(SAMPLE_LOCATIONS);
    if (p === '/api/garden/profile') {
      return json({
        user_id: 'household',
        family_size: 7,
        hardiness_zone: '5b',
        zone_source: 'auto',
        last_spring_frost_mmdd: '05-15',
        first_fall_frost_mmdd: '10-15',
        units: 'imperial',
        preservation_prefs: null,
        daily_digest_time: '08:00',
        yard_canvas_json: null,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      });
    }
    if (p === '/api/garden/streaks') {
      return json({ user_id: 'admin', current_streak: 5, longest_streak: 12, total_logs: 24 });
    }
    if (p === '/api/garden/species') {
      return json({ category: null, usda_zone: null, count: SAMPLE_SPECIES.length, results: SAMPLE_SPECIES, seed_present: true });
    }
    if (p === '/api/garden/species/search') {
      return json({ query: url.searchParams.get('q') || '', category: null, count: 1, results: [SAMPLE_SPECIES[0]], seed_present: true });
    }
    if (p === '/api/garden/pantry-goals') {
      return json({
        count: 1,
        goals: [
          {
            id: 'goal-marinara',
            item: 'Marinara sauce',
            crop_refs_json: '["tomato_roma"]',
            target_qty: 40,
            unit: 'quarts',
            method: 'water_bath_canning',
            season_year: 2027,
            notes: null,
            created_at: '2026-11-20T00:00:00Z',
            updated_at: '2026-11-20T00:00:00Z',
          },
        ],
      });
    }
    if (p === '/api/garden/harvest/forecast') {
      return json({
        generated_on: '2026-11-20',
        window_days: 28,
        weather_used: true,
        gdd_adjusted_count: 1,
        count: 2,
        items: [
          {
            plant_id: 'plant-tomato', nickname: 'Roma Row', common_name: 'Roma (Plum) Tomato',
            category: 'vegetable', species_ref: 'tomato_roma', planted_date: '2026-06-01',
            estimated_ready_date: '2026-11-24', harvest_window_end: '2026-12-24',
            days_until_ready: 4, ready_now: false, gdd_adjusted: true, gdd_base_f: 50,
            gdd_daily_rate: 12.5, gdd_shift_days: -3, days_to_maturity: 75,
            harvest_window_days: 30, culinary_aliases: ['tomato'],
            preservation_methods: ['water_bath_canning', 'dehydrating', 'freezing'],
            usda_acidity_class: 'high_acid', blanching_minutes: null,
            location_id: 'loc-bed-north', assigned_to: 'parent',
          },
          {
            plant_id: 'plant-beans', nickname: null, common_name: 'Bush Green Bean',
            category: 'vegetable', species_ref: 'green_bean_bush', planted_date: '2026-08-01',
            estimated_ready_date: '2026-12-05', harvest_window_end: '2027-01-04',
            days_until_ready: 15, ready_now: false, gdd_adjusted: false, gdd_base_f: 50,
            gdd_daily_rate: 0, gdd_shift_days: 0, days_to_maturity: 55,
            harvest_window_days: 30, culinary_aliases: ['green bean', 'string bean'],
            preservation_methods: ['freezing', 'pressure_canning', 'dehydrating'],
            usda_acidity_class: 'low_acid', blanching_minutes: 3,
            location_id: 'loc-bed-north', assigned_to: null,
          },
        ],
      });
    }
    if (p === '/api/garden/preservation/advice') {
      return json({
        generated_on: '2026-11-20',
        crop_count: 2,
        plant_count: 3,
        crops: [
          {
            species_ref: 'green_bean_bush', common_name: 'Bush Green Bean', category: 'vegetable',
            culinary_aliases: ['green bean'], usda_acidity_class: 'low_acid',
            safety: {
              level: 'pressure_canner_required',
              title: 'Low-acid — Pressure Canner Required',
              message: 'Pressure canning is required for plain low-acid vegetables. Never water-bath can unacidified low-acid food: botulism risk. Follow a tested USDA recipe.',
              source: 'USDA Complete Guide to Home Canning / NCHFP / Ball Blue Book',
            },
            blanching_minutes: 3, herb_preservation_tips: null, preservation_conversion: null,
            plant_ids: ['plant-beans'], plant_count: 2,
            methods: [
              { method: 'freezing', recorded_as: 'freezing', label: 'Freezing', storage: 'Chest freezer', use_by_months: 10, blanching_minutes: 3, requires_acidification: false, supplies: ['Freezer bags or containers'] },
              { method: 'pressure_canning', recorded_as: 'pressure_canning', label: 'Pressure canning', storage: 'Cool, dark shelf', use_by_months: 15, blanching_minutes: null, requires_acidification: false, supplies: ['Quart canning jars'] },
            ],
          },
          {
            species_ref: 'tomato_roma', common_name: 'Roma (Plum) Tomato', category: 'vegetable',
            culinary_aliases: ['tomato'], usda_acidity_class: 'high_acid',
            safety: {
              level: 'water_bath_safe',
              title: 'High-acid — Water-Bath Safe',
              message: 'Water-bath canning is safe for high-acid foods. Tomatoes need added acid: bottled lemon juice or citric acid per USDA.',
              source: 'USDA Complete Guide to Home Canning / NCHFP / Ball Blue Book',
            },
            blanching_minutes: null, herb_preservation_tips: null, preservation_conversion: null,
            plant_ids: ['plant-tomato'], plant_count: 1,
            methods: [
              { method: 'water_bath_canning', recorded_as: 'water_bath_canning', label: 'Water-bath canning', storage: 'Cool, dark shelf', use_by_months: 15, blanching_minutes: null, requires_acidification: false, supplies: ['Quart canning jars'] },
            ],
          },
        ],
        combos: [
          {
            key: 'marinara', label: 'Marinara sauce', method: 'water_bath_canning',
            method_label: 'Water-bath canning', note: 'Acidify with bottled lemon juice or citric acid.',
            required: ['tomato'], optional: ['basil', 'garlic'], matched_crops: ['Roma (Plum) Tomato'],
            missing_crops: [], optional_matched: [], optional_missing: ['basil', 'garlic'],
            ready: true, matched_count: 1, required_count: 1,
            safety: {
              level: 'water_bath_safe', title: 'High-acid — Water-Bath Safe',
              message: 'Water-bath canning is safe for high-acid foods.',
              source: 'USDA Complete Guide to Home Canning / NCHFP / Ball Blue Book',
            },
            supplies: ['Quart canning jars'],
          },
        ],
        prep_checklist: {
          horizon_days: 21,
          crops: ['Roma (Plum) Tomato', 'Bush Green Bean'],
          methods: [{ method: 'freezing', label: 'Freezing' }, { method: 'water_bath_canning', label: 'Water-bath canning' }],
          combos: ['Marinara sauce'],
          count: 3,
          items: ['Quart canning jars', 'Freezer bags or containers', 'Bottled lemon juice or citric acid'],
          categories: [
            { key: 'jars_and_lids', label: 'Jars & lids', items: ['Quart canning jars'] },
            { key: 'acid_and_safety', label: 'Acid & safety', items: ['Bottled lemon juice or citric acid'] },
            { key: 'freezer', label: 'Freezer', items: ['Freezer bags or containers'] },
          ],
        },
        safety_sources: ['USDA Complete Guide to Home Canning / NCHFP / Ball Blue Book'],
      });
    }
    if (p === '/api/garden/pantry-batches') {
      if (method === 'POST') {
        return json({ id: 'batch-new', label: 'Dill pickles', method: 'water_bath_canning', qty_initial: 6, qty_remaining: 6, unit: 'quarts' }, 201);
      }
      return json({
        generated_on: '2026-11-20',
        include_empty: false,
        goal_id: null,
        count: 2,
        use_first_count: 1,
        batches: [
          {
            id: 'batch-1', goal_id: 'goal-2026', label: 'Marinara sauce', method: 'water_bath_canning',
            method_label: 'Water-bath canning', batch_date: '2026-09-15', qty_initial: 12, qty_remaining: 8,
            unit: 'quarts', storage_location: 'Basement shelf', use_by_date: '2027-12-15',
            logged_by: 'parent', notes: null, created_at: '2026-09-15T00:00:00Z', updated_at: '2026-09-15T00:00:00Z',
            days_until_use_by: 30, past_use_by: false, use_first: true, oldest_in_label: true,
          },
          {
            id: 'batch-2', goal_id: null, label: 'Frozen green beans', method: 'freezing',
            method_label: 'Freezing', batch_date: '2026-10-01', qty_initial: 20, qty_remaining: 16,
            unit: 'bags', storage_location: 'Chest freezer', use_by_date: '2027-08-01',
            logged_by: 'parent', notes: null, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
            days_until_use_by: 254, past_use_by: false, use_first: false, oldest_in_label: true,
          },
        ],
        goal_progress: [
          {
            goal_id: 'goal-2026', item: 'Marinara sauce', unit: 'quarts', target_qty: 40,
            qty_put_up: 28, qty_remaining: 8, percent_complete: 70, batch_count: 1,
            unit_mismatch: false, mismatched_units: [], season_year: 2026,
          },
        ],
      });
    }
    if (/^\/api\/garden\/pantry-batches\/[^/]+\/use$/.test(p)) {
      return json({ id: p.split('/')[4], label: 'Marinara sauce', method: 'water_bath_canning', qty_initial: 12, qty_remaining: 7, unit: 'quarts', used_by: 'parent', qty_used: 1 });
    }
    if (/^\/api\/garden\/pantry-batches\/[^/]+$/.test(p)) {
      if (method === 'DELETE') return route.fulfill({ status: 204, body: '' });
      return json({ id: p.split('/').pop(), label: 'Marinara sauce', method: 'water_bath_canning', qty_initial: 12, qty_remaining: 7, unit: 'quarts' });
    }
    if (p === '/api/garden/pantry-goals/calculate') {
      return json({
        item: 'Marinara sauce',
        method: 'water_bath_canning',
        unit: 'quarts',
        target_qty: 40,
        total_raw_lbs_needed: 120,
        total_plant_count: 12,
        total_sqft: 12,
        crops: [{ species_ref: 'tomato_roma', common_name: 'Roma Tomato', raw_lbs_needed: 120, plant_count: 12, sqft: 12 }],
      });
    }
    if (p === '/api/garden/plans') return json({ count: 1, plans: [SAMPLE_PLAN] });
    if (p === '/api/garden/plans/plan-2027/beds/loc-bed-north/cells') {
      return json({
        plan_id: 'plan-2027',
        bed_id: 'loc-bed-north',
        cells: [
          { id: 'c1', col: 0, row: 7, species_ref: 'tomato_roma', quantity: 1 },
          { id: 'c2', col: 1, row: 7, species_ref: 'basil_sweet', quantity: 1 },
        ],
      });
    }
    if (p === '/api/garden/plans/plan-2027/checks') {
      return json({
        plan_id: 'plan-2027',
        warning_count: 0,
        checks: [
          { cell_id: 'c1', col: 0, row: 7, species_ref: 'tomato_roma', bed_id: 'loc-bed-north', kind: 'companion', message: 'Sweet Basil pairs well with Roma Tomato' },
        ],
      });
    }
    if (p === '/api/garden/plans/plan-2027/timeline') {
      return json({
        plan_id: 'plan-2027',
        plan_name: SAMPLE_PLAN.name,
        season: 'spring_summer',
        year: 2027,
        spring_frost_mmdd: '05-15',
        first_fall_frost_mmdd: '10-15',
        timeline: [
          {
            cell_id: 'c1',
            species_ref: 'tomato_roma',
            common_name: 'Roma Tomato',
            milestones: [
              { stage: 'Start seeds indoors', week_offset: 6, mmdd: '04-03' },
              { stage: 'Transplant / Direct sow', week_offset: -2, mmdd: '05-29' },
            ],
          },
        ],
      });
    }
    if (p === '/api/garden/plants/plant-monty/logs') return json({ logs: [] });
    // Phase 7: the growth journal, with one real thumbnail to lay out.
    if (p === '/api/garden/plants/plant-monty/photos') {
      if (method === 'POST') {
        return json({ photo: { id: 'photo-new', plant_id: 'plant-monty', kind: 'progress', taken_at: '2026-11-20T09:00:00Z', taken_by: 'parent', thumb_path: null, nextcloud_path: null, ai_summary_json: null, notes: null, created_at: '2026-11-20T09:00:00Z' }, nextcloud_warning: null }, 201);
      }
      return json({
        plant_id: 'plant-monty',
        count: 2,
        photos: [
          {
            id: 'photo-1', plant_id: 'plant-monty', kind: 'progress',
            thumb_path: 'photo-1.jpg', nextcloud_path: 'Garden/Photos/plant-monty/a.jpg',
            taken_at: '2026-11-18T09:00:00Z', taken_by: 'parent', ai_summary_json: null,
            notes: 'New leaf unfurling', created_at: '2026-11-18T09:00:00Z',
          },
          {
            id: 'photo-2', plant_id: 'plant-monty', kind: 'diagnosis',
            thumb_path: 'photo-2.jpg', nextcloud_path: 'Garden/Photos/plant-monty/b.jpg',
            taken_at: '2026-11-12T09:00:00Z', taken_by: 'parent',
            ai_summary_json: '{"urgency": "medium", "summary": "Overwatered."}',
            notes: null, created_at: '2026-11-12T09:00:00Z',
          },
        ],
      });
    }
    if (/^\/api\/garden\/plants\/[^/]+\/photos\/[^/]+\/thumb$/.test(p)) {
      return route.fulfill({ status: 200, contentType: 'image/jpeg', body: TINY_JPEG });
    }
    if (/^\/api\/garden\/plants\/[^/]+\/photos\/[^/]+$/.test(p) && method === 'DELETE') {
      return route.fulfill({ status: 204, body: '' });
    }
    if (p === '/api/garden/vision/identify') {
      return json({
        status: 'ok',
        identified: { common_name: 'Monstera', scientific_name: 'Monstera deliciosa', category: 'houseplant', confidence: 'high', is_seed_packet: false, notes: 'Split leaves', description: 'A climber.' },
        matches: [{ key: 'monstera_deliciosa', category: 'houseplant', common_name: 'Monstera (Swiss Cheese Plant)', scientific_name: 'Monstera deliciosa', description: 'Fenestrated climber.' }],
        in_catalog: true,
        can_draft_profile: false,
        uncertain: [],
      });
    }
    if (p === '/api/garden/harvests') return json({ harvests: [], count: 0 });
    if (method === 'POST' && p === '/api/garden/profile/detect-zone') {
      return json({ zone: '5b', zone_source: 'auto', last_spring_frost_mmdd: '05-15', first_fall_frost_mmdd: '10-15' });
    }
    return route.fallback();
  });

  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error' && /React UI error|Uncaught/.test(m.text())) errors.push(m.text().slice(0, 300));
  });
  await page.goto(path);
  return { home, errors, findings: [] };
}

async function auditScreen(page: Page, info: TestInfo, run: Run, scope: Locator, where: string): Promise<void> {
  await settle(page);
  await openAllDetails(scope);
  await settle(page);
  run.findings.push(...await screenFindings(page, scope, where));
  run.findings.push(...await overlappingControls(scope, where));
  await shot(page, info, where.replace(/[^a-z0-9]+/gi, '-'));
}

test.describe('Garden UI on every form factor', () => {
  test.describe.configure({ timeout: 120_000 });

  test('Today, Plants, Planner tabs, Bed Grid, modals, and Plant Detail have no overlaps or overflow', async ({ page }, info) => {
    const run = await openGarden(page, '/garden');
    const main = page.locator('main');

    // 1. Today tab (with weather banner, harvest-ready card, and due room task)
    await expect(main.getByText('Monty').first()).toBeVisible();
    await auditScreen(page, info, run, main, 'garden-today');

    // 2. Add Plant modal
    await main.getByRole('button', { name: 'Add Plant' }).click();
    await expect(page.getByText('Search species')).toBeVisible();
    const modal = page.getByRole('dialog').last();
    await auditScreen(page, info, run, (await modal.count()) ? modal : page.locator('body'), 'garden-add-modal');
    await page.getByRole('button', { name: 'Cancel' }).click();

    // 3. Plants tab
    await main.getByRole('button', { name: 'Plants' }).click();
    await expect(main.getByText('Sir Basil').first()).toBeVisible();
    await auditScreen(page, info, run, main, 'garden-plants');

    // 4. Planner tab (Zone, Sizing, Pantry Goals, Plans, Yard Map, Bed Grid, Timeline)
    await main.getByRole('button', { name: 'Planner' }).click();
    await expect(main.getByText('Household Climate')).toBeVisible();
    await auditScreen(page, info, run, main, 'garden-planner');

    // Open Pantry goals sub-tab & Bed Grid Editor
    await main.getByRole('button', { name: 'Pantry goals' }).click();
    await expect(main.getByText('Marinara sauce')).toBeVisible();
    await main.getByRole('button', { name: 'Open North Raised Bed bed grid' }).click();
    await expect(main.getByText(/Square-foot grid/i)).toBeVisible();
    await auditScreen(page, info, run, main, 'garden-planner-bed-grid');

    // 5. Pantry tab (Coming Ready Soon, USDA callouts, combos, goal progress, batches)
    await main.getByRole('button', { name: 'Pantry', exact: true }).click();
    await expect(main.getByRole('heading', { name: 'Coming Ready Soon' })).toBeVisible();
    await expect(main.getByText('Low-acid — Pressure Canner Required')).toBeVisible();
    await expect(main.getByText(/28 \/ 40 quarts put up \(70%\)/)).toBeVisible();
    await auditScreen(page, info, run, main, 'garden-pantry');

    // 6. Plant detail page
    await page.goto('/garden/plants/plant-monty');
    await expect(main.getByText('Care Sheet')).toBeVisible();
    await auditScreen(page, info, run, main, 'garden-plant-detail');

    expect(run.findings).toEqual([]);
    expect(run.home.unmocked).toEqual([]);
    expect(run.errors).toEqual([]);
  });
});
