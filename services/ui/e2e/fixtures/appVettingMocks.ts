/**
 * App vetting answers for the hermetic specs (docs/APP_VETTING.md): a child's
 * Apps card and the verdict on an app request. Made-up apps with long names,
 * a long review quote and every state a row can be in, so a card that lets
 * anything run past its edge shows it.
 */

const DAY = 86_400_000;

function policy(username: string) {
  return {
    username, display_name: username, birth_date: '2016-01-01', age: 10, default_max_rating: 'Everyone 10+', max_rating: null,
    effective_max_rating: 'Everyone 10+', ratings: ['Everyone', 'Everyone 10+', 'Teen', 'Mature 17+', 'Adults only 18+'],
    apps: {}, unused_days: 30, dismissed: {}, kept: {}, blocked_apps: [], uninstall_apps: [], can_uninstall: true,
    notify_level: 'high',
  };
}

function app(pkg: string, label: string, extra: Record<string, unknown> = {}) {
  return {
    package: pkg, label, lookup: 'found', review_state: 'ready', rating: 'Everyone', rating_descriptors: [], interactive: [],
    limit: 'Everyone 10+', over_age: false, ads: false, in_app_purchases: false, data_safety: {}, stars: 4.4, installs: '1,000,000+',
    summary: 'A calm puzzle game with no chat.', concerns: [], flags: [], level: null, override: null, fingerprint: '',
    dismissed: false, store: 'Google Play', installer: 'com.android.vending', first_install: Date.now() - 300 * DAY,
    last_update: Date.now() - 20 * DAY, last_used: Date.now() - DAY, permissions: [], size_bytes: 88_000_000,
    devices: ['kid-phone-0000000001'], attention: [], age_source: null, common_sense: null, common_sense_over_age: null,
    common_sense_limit: 10, play_over_age: false, review_excerpts: [], reviews_read: {}, security: [], security_level: null,
    malware_check: { state: 'not_set_up', reason: 'Not set up' }, special_access: [], capabilities: [], ...extra,
  };
}

const FLAGS = [
  { reason: 'over_age', category: 'rating', level: 'high', text: 'Rated Teen, above the Everyone 10+ limit' },
  { reason: 'concern', category: 'contact_strangers', level: 'high',
    text: 'Players can message anyone, and reviews describe strangers sending friend requests to young players.' },
];

const SECURITY_FLAGS = [
  { reason: 'sideloaded_risky', category: 'security', level: 'severe',
    text: 'Not from Google Play, and it has accessibility access (switched on), reading text messages' },
];

export function guardianApps(username: string) {
  return {
    username,
    policy: policy(username),
    devices: [{ device: 'kid-phone-0000000001', reported_at: new Date().toISOString(), apps: 4, usage_access: true }],
    apps: [
      app('com.example.blockworld', 'Block World Builders Online Adventure Deluxe Edition', {
        rating: 'Teen', over_age: true, in_app_purchases: true, ads: true, level: 'high', fingerprint: '0123456789abcdef',
        interactive: ['Users Interact', 'Shares Location', 'In-Game Purchases (Includes Random Items)'],
        summary: 'A building game with open voice and text chat between players of any age, and loot boxes.',
        concerns: [{ category: 'contact_strangers', label: 'Contact with strangers or chat', severity: 'high',
          why: 'Strangers can message players.', evidence: ['Random people kept sending my son friend requests and messages every single day, even after we reported them'] }],
        data_safety: { shares_data: true, shared_types: 'Location, Personal info and 3 others', collects_data: true, collected_types: 'Location, Personal info and 7 others' },
        flags: FLAGS, attention: FLAGS, age_source: 'common_sense', play_over_age: true, common_sense_over_age: true,
        common_sense: { status: 'found', reason: null, url: 'https://www.commonsensemedia.org/app-reviews/block-world-builders',
          name: 'Block World Builders', kind: 'game', age: 13, age_label: 'age 13+', quality_stars: 3,
          parents_need_to_know: 'Parents need to know that Block World Builders lets players talk to anyone by voice and text, sells random item boxes for real money, and shares a rough location with other players by default.',
          parents_say: { age: 12, reviews: 41, summary: null }, kids_say: { age: 11, reviews: 27, summary: null }, published: '2025-03-01' },
        review_excerpts: [
          { category: 'contact_strangers', stars: 1, at: '2026-10-02', source: 'lowest',
            text: 'Random people kept sending my son friend requests and messages every single day, even after we reported them' },
          { category: 'contact_strangers', stars: 2, at: '2026-11-01', source: 'newest', text: 'Chat is full of adults asking kids where they live.' },
        ],
        reviews_read: { relevant: 20, newest: 15, lowest: 12, state: 'done', errors: {} },
      }),
      app('com.example.screenhelper', 'Screen Helper Pro Accessibility Companion', {
        lookup: 'not_on_play', review_state: 'not_needed', rating: null, over_age: null, summary: null, store: null, installer: null,
        level: 'severe', capabilities: ['accessibility', 'sms_read'], special_access: ['accessibility'], security_level: 'severe',
        security: [
          { check: 'risky_access', key: 'accessibility', active: true, severity: 'high',
            text: 'It can read and control everything on the screen (accessibility service), and it is switched on' },
          { check: 'risky_access', key: 'sms_read', active: false, severity: 'high', text: 'It can read text messages' },
          { check: 'sideloaded', key: 'unknown_installer', severity: 'medium',
            text: 'Not installed from Google Play, and Android does not say where it came from' },
        ],
        flags: SECURITY_FLAGS, attention: SECURITY_FLAGS,
      }),
      app('com.example.calmpuzzles', 'Calm Puzzles'),
      app('com.example.newthing', 'Brand New Thing', { lookup: 'pending', review_state: 'checking', rating: null, over_age: null, summary: null }),
      app('com.example.sideloaded', 'Sideloaded Tool', { lookup: 'not_on_play', review_state: 'not_needed', rating: null,
        over_age: null, summary: null, store: null, installer: null, permissions: ['location', 'microphone'] }),
    ],
    attention: 2,
    unused: [{ package: 'com.example.oldracer', label: 'Old Racer Turbo Championship Edition', last_used: Date.now() - 95 * DAY,
      first_install: Date.now() - 400 * DAY, size_bytes: 1_400_000_000, days_unused: 95, never_used: false, devices: ['kid-phone-0000000001'] }],
    lookups: { enabled: true, paused_until: null,
      malware: { state: 'not_set_up', reason: 'Not set up', paused_until: null, paused_reason: null } },
    stalkerware_list: { source: 'https://github.com/AssoEchap/stalkerware-indicators', commit: 'abc', commit_date: '2026-10-20',
      license: 'CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)',
      attribution: 'Stalkerware Indicators by Echap and contributors, https://github.com/AssoEchap/stalkerware-indicators' },
  };
}

/** The verdict a parent sees on a child's app request. */
export const REQUEST_VETTING = {
  lookup: 'found', review_state: 'ready', rating: 'Teen', limit: 'Everyone 10+', over_age: true, ads: true, in_app_purchases: true,
  interactive: ['Users Interact'], summary: 'A building game with open chat between players of any age.',
  concerns: [{ label: 'Contact with strangers or chat', severity: 'high', why: 'Strangers can message players.' },
    { label: 'Gambling or loot boxes', severity: 'medium', why: 'Random item boxes are sold for real money.' }],
  line: 'Rated Teen, above the Everyone 10+ limit.',
};

/** The app-vetting routes: the card, and the parents' changes (answered with the policy). */
export function appVettingAnswer(p: string, method: string): unknown | undefined {
  const match = p.match(/^\/api\/guardian\/apps\/([^/]+)(\/.*)?$/);
  if (!match) return undefined;
  const username = decodeURIComponent(match[1]);
  return method === 'GET' && !match[2] ? guardianApps(username) : policy(username);
}
