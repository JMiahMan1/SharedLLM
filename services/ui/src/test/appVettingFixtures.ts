// Fake app-vetting data for tests (made-up apps and people, no family data).
import type { AppPolicy, AppVerdict, GuardianApps } from '../types/api';

export const POLICY: AppPolicy = {
  username: 'kid',
  display_name: 'Sam',
  birth_date: '2016-01-01',
  age: 10,
  default_max_rating: 'Everyone 10+',
  max_rating: null,
  effective_max_rating: 'Everyone 10+',
  ratings: ['Everyone', 'Everyone 10+', 'Teen', 'Mature 17+', 'Adults only 18+'],
  apps: {},
  unused_days: 30,
  dismissed: {},
  kept: {},
  blocked_apps: [],
  uninstall_apps: [],
  can_uninstall: false,
  notify_level: 'high',
};

const BASE: AppVerdict = {
  package: 'com.example.app', label: 'Example', lookup: 'found', review_state: 'ready', rating: 'Everyone',
  rating_descriptors: [], interactive: [], limit: 'Everyone 10+', over_age: false, ads: false, in_app_purchases: false,
  data_safety: {}, stars: 4.2, installs: '1,000+', summary: 'A quiet puzzle game.', concerns: [], flags: [], level: null,
  override: null, fingerprint: '', dismissed: false, store: 'Google Play', installer: 'com.android.vending',
  first_install: 1_700_000_000_000, last_update: 1_700_000_000_000, last_used: Date.now() - 86_400_000,
  permissions: [], size_bytes: 52_428_800, devices: ['phonekey00000001'], attention: [],
  age_source: null, common_sense: null, common_sense_over_age: null, common_sense_limit: 10, play_over_age: false,
  review_excerpts: [], reviews_read: {}, security: [], security_level: null,
  malware_check: { state: 'not_known', reason: null }, special_access: [], capabilities: [],
};

export function verdict(overrides: Partial<AppVerdict>): AppVerdict {
  return { ...BASE, ...overrides };
}

export const CHAT_GAME = verdict({
  package: 'com.example.blockworld', label: 'Block World With A Very Long Name That Should Wrap Nicely', rating: 'Teen',
  over_age: true, in_app_purchases: true, interactive: ['Users Interact', 'In-Game Purchases'], level: 'high',
  fingerprint: '0123456789abcdef', summary: 'A building game where players chat with anyone.',
  concerns: [{ category: 'contact_strangers', label: 'Contact with strangers or chat', severity: 'high',
    why: 'Strangers can message players.', evidence: ['Random people kept sending friend requests'] }],
  age_source: 'common_sense', play_over_age: true, common_sense_over_age: true,
  common_sense: { status: 'found', reason: null, url: 'https://www.commonsensemedia.org/app-reviews/block-world', name: 'Block World',
    kind: 'game', age: 13, age_label: 'age 13+', quality_stars: 3,
    parents_need_to_know: 'Parents need to know that players can chat with strangers and buy items.',
    parents_say: { age: 12, reviews: 40, summary: null }, kids_say: { age: 11, reviews: 25, summary: null }, published: '2025-03-01' },
  review_excerpts: [{ category: 'contact_strangers', stars: 1, at: '2026-10-02', text: 'Random people kept sending friend requests', source: 'lowest' }],
  reviews_read: { relevant: 20, newest: 15, lowest: 12, state: 'done', errors: {} },
  flags: [
    { reason: 'over_age', category: 'rating', level: 'high', text: 'Rated Teen, above the Everyone 10+ limit' },
    { reason: 'concern', category: 'contact_strangers', level: 'high', text: 'Strangers can message players.' },
  ],
  attention: [
    { reason: 'over_age', category: 'rating', level: 'high', text: 'Rated Teen, above the Everyone 10+ limit' },
    { reason: 'concern', category: 'contact_strangers', level: 'high', text: 'Strangers can message players.' },
  ],
});

export const SIDELOADED = verdict({
  package: 'com.example.helper', label: 'Helper', lookup: 'not_on_play', store: null, installer: null, rating: null,
  over_age: null, summary: null, level: 'severe', capabilities: ['accessibility', 'sms_read'], special_access: ['accessibility'],
  security_level: 'severe', malware_check: { state: 'not_set_up', reason: 'Not set up' },
  security: [
    { check: 'risky_access', key: 'accessibility', active: true, severity: 'high',
      text: 'It can read and control everything on the screen (accessibility service), and it is switched on' },
    { check: 'sideloaded', key: 'unknown_installer', severity: 'medium', text: 'Not installed from Google Play, and Android does not say where it came from' },
  ],
  flags: [{ reason: 'sideloaded_risky', category: 'security', level: 'severe', text: 'Not from Google Play, and it has accessibility access (switched on)' }],
  attention: [{ reason: 'sideloaded_risky', category: 'security', level: 'severe', text: 'Not from Google Play, and it has accessibility access (switched on)' }],
});

export const CHECKING = verdict({ package: 'com.example.newapp', label: 'New App', lookup: 'pending', review_state: 'checking',
  rating: null, over_age: null, summary: null });

export const APPS: GuardianApps = {
  username: 'kid',
  policy: POLICY,
  devices: [{ device: 'phonekey00000001', reported_at: '2026-11-15T10:00:00Z', apps: 3, usage_access: true }],
  apps: [SIDELOADED, CHAT_GAME, verdict({}), CHECKING],
  attention: 2,
  unused: [{ package: 'com.example.oldgame', label: 'Old Game', last_used: Date.now() - 90 * 86_400_000,
    first_install: 1_650_000_000_000, size_bytes: 314_572_800, days_unused: 90, never_used: false, devices: ['phonekey00000001'] }],
  lookups: { enabled: true, paused_until: null,
    malware: { state: 'not_set_up', reason: 'Not set up', paused_until: null, paused_reason: null } },
  stalkerware_list: { source: 'https://github.com/AssoEchap/stalkerware-indicators', commit: 'abc', commit_date: '2026-10-20',
    license: 'CC BY 4.0', attribution: 'Stalkerware Indicators by Echap and contributors' },
};
