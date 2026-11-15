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
    devices: ['kid-phone-0000000001'], attention: [], ...extra,
  };
}

const FLAGS = [
  { reason: 'over_age', category: 'rating', level: 'high', text: 'Rated Teen, above the Everyone 10+ limit' },
  { reason: 'concern', category: 'contact_strangers', level: 'high',
    text: 'Players can message anyone, and reviews describe strangers sending friend requests to young players.' },
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
        flags: FLAGS, attention: FLAGS,
      }),
      app('com.example.calmpuzzles', 'Calm Puzzles'),
      app('com.example.newthing', 'Brand New Thing', { lookup: 'pending', review_state: 'checking', rating: null, over_age: null, summary: null }),
      app('com.example.sideloaded', 'Sideloaded Tool', { lookup: 'not_on_play', review_state: 'not_needed', rating: null,
        over_age: null, summary: null, store: null, installer: null, permissions: ['location', 'microphone'] }),
    ],
    attention: 1,
    unused: [{ package: 'com.example.oldracer', label: 'Old Racer Turbo Championship Edition', last_used: Date.now() - 95 * DAY,
      first_install: Date.now() - 400 * DAY, size_bytes: 1_400_000_000, days_unused: 95, never_used: false, devices: ['kid-phone-0000000001'] }],
    lookups: { enabled: true, paused_until: null },
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
