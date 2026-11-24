import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import type { Page, Route } from '@playwright/test';
import { isServerCall, INTEGRATIONS_STATUS_PATH, integrationsStatus } from './serverCalls';

/**
 * Made-up content for the "library" pages (Notes, Recipes, Media, Knowledge,
 * Workspaces, Artifacts, Docs and the Lab), answered in the browser. Register
 * after mockHome (and mockHealth / mockBible) so these answer first; anything
 * not listed here falls back to them. Names are invented, and the content is
 * long on purpose: long titles, long lines and one unbroken word or file name
 * on each page, so a layout that cannot wrap shows up.
 */

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MEDIA_DIR = path.join(__dirname, 'media');
/** The media pages' own fixtures (shared with e2e/media/*). */
const media = (name: string): unknown => JSON.parse(fs.readFileSync(path.join(MEDIA_DIR, `${name}.json`), 'utf-8'));

const PNG_1X1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64',
);

const NOW = Date.now();
const iso = (offsetMin = 0) => new Date(NOW + offsetMin * 60_000).toISOString();

/** One word with no place to break it, the kind a pasted link or file name brings. */
export const UNBROKEN = 'Supercalifragilisticexpialidocious-and-then-some-more-unbroken-text';

// ---------------------------------------------------------------- Notes

export const LONG_NOTE_TITLE = 'Packing list for the long summer road trip to visit the grandparents and cousins';
const LONG_NOTE_BODY = [
  `# ${LONG_NOTE_TITLE}`,
  'Category: Notes',
  '',
  'Everything we need for the trip, in the order the car gets packed. #travel #summer',
  '',
  ...Array.from({ length: 24 }, (_, i) => `- [${i % 5 === 0 ? 'x' : ' '}] Item ${i + 1}: something to remember to pack, written out at length so it wraps`),
  '',
  `A pasted link with no spaces: ${UNBROKEN}`,
  '',
  ...Array.from({ length: 10 }, (_, i) => `Paragraph ${i + 1}. A made-up line of placeholder text, long enough to run across the whole note and onto the next line.`),
].join('\n');

const NOTES = [
  { title: LONG_NOTE_TITLE, path: `Notes/${LONG_NOTE_TITLE}.md`, size: LONG_NOTE_BODY.length, modified: iso(-30), content: LONG_NOTE_BODY },
  { title: 'Groceries', path: 'Notes/Groceries.md', size: 80, modified: iso(-90),
    content: '# Groceries\nCategory: Notes\n\n- [ ] Milk\n- [ ] Bread\n- [x] Apples\n\n#shopping' },
  { title: UNBROKEN, path: `Notes/${UNBROKEN}.md`, size: 40, modified: iso(-600),
    content: `# ${UNBROKEN}\nCategory: Notes\n\nA note whose name has nowhere to break.` },
  { title: 'Ideas', path: 'Notes/Ideas.md', size: 60, modified: iso(-2000),
    content: '# Ideas\nCategory: Notes\n\nPaint the fence. Plant tomatoes. #home' },
];

const NOTES_META = { version: 1, notes: { [`Notes/${LONG_NOTE_TITLE}.md`]: { pinned: true, color: 'yellow' } } };

// ---------------------------------------------------------------- Recipes

const recipe = (title: string, extra: Record<string, unknown> = {}) => ({
  title, path: `Recipes/${title}.md`, modified: iso(-120), tags: ['dinner'], meal: 'main', proteins: ['chicken'],
  total_minutes: 45, servings: 4, calories: 520, ingredients: 9, level: 2, owner: 'parent', owner_label: 'Parent',
  can_change: true, source: 'https://recipes.example.test/a-very-long-recipe-address-for-small-screens', image: null,
  staples: ['rice'], rating: 4.5, ratings: 3, my_rating: 5, made: 6, made_by_me: 2, last_made: iso(-60 * 24 * 3),
  ...extra,
});

export const LONG_RECIPE_TITLE = 'Grandmother’s slow-cooked chicken and vegetable stew with herb dumplings';
const RECIPES = [
  recipe(LONG_RECIPE_TITLE, { meal: 'soup', image: 'photo.jpg' }),
  recipe('Pancakes', { meal: 'breakfast', proteins: ['vegetarian'], total_minutes: 20, rating: 5, ratings: 4, made: 14 }),
  recipe(UNBROKEN, { meal: 'dessert', proteins: [], total_minutes: null, servings: null, calories: null, rating: null, ratings: 0,
    my_rating: null, made: 0, made_by_me: 0, last_made: null, can_change: false, owner: 'house', owner_label: 'an admin' }),
  recipe('Beef tacos', { proteins: ['beef'], total_minutes: 30 }),
  recipe('Garden salad', { meal: 'salad', proteins: ['vegetarian'], total_minutes: 10, image: 'salad.jpg' }),
];

const RECIPE_BODY = [
  'Tags: #dinner #family-favourite',
  'Times: prep 20 min · cook 3 hr · servings 6',
  'Source: https://recipes.example.test/a-very-long-recipe-address-for-small-screens',
  '',
  '## Ingredients',
  ...['2 lb chicken thighs, skin removed and cut into large pieces', '3 carrots, peeled and sliced', '2 potatoes, cubed',
    '1 onion, chopped', '4 cups chicken stock', '1 cup flour', '2 tsp baking powder', '1 tbsp chopped parsley',
    `a pinch of ${UNBROKEN}`].map((line) => `- [ ] ${line}`),
  '',
  '## Steps',
  ...Array.from({ length: 6 }, (_, i) => `${i + 1}. A made-up step of the recipe, written out in full so it runs over several lines on a phone.`),
].join('\n');

const MEAL_PLAN = [
  '# Meal plan', 'Category: Recipes', '', '#meal-plan', '',
  `- [x] Mon · ${LONG_RECIPE_TITLE}`, '- [ ] Tue · Beef tacos', '- [ ] Wed', `- [ ] Thu · ${UNBROKEN}`,
  '- [ ] Fri · Pancakes', '- [ ] Sat', '- [ ] Sun',
].join('\n');

const SHOPPING = [
  '# Shopping list', 'Category: Recipes', '',
  '- [ ] 2 lb chicken thighs, skin removed and cut into large pieces', '- [x] 3 carrots', '- [ ] 4 cups chicken stock',
  `- [ ] ${UNBROKEN}`, '- [ ] Tortillas',
].join('\n');

const SUGGESTION = {
  id: 's1', path: 'Recipes/.suggestions/s1.md', recipe: RECIPES[2].path, recipe_title: UNBROKEN, by: 'sam', at: iso(-300),
  message: 'Less sugar, please, and a note about letting the dough rest for a good long while before it is rolled out.',
  content: '## Ingredients\n- [ ] 1 cup sugar', current: '## Ingredients\n- [ ] 2 cups sugar', owner: 'house', owner_label: 'an admin',
  mine: false, can_review: true,
};

// ---------------------------------------------------------------- Knowledge

const BOOKS = [
  { id: 1, title: 'A Made-up Commentary on the Gospels for Families Reading Together in the Evenings', authors: ['Test Author', 'Another Author'],
    tags: ['Bible study', 'Family'], series: 'Evening Readings', series_index: 1, formats: ['EPUB', 'PDF'], readable: true, path: 'Author/Book (1)' },
  { id: 2, title: UNBROKEN, authors: ['Test Author'], tags: [], series: null, series_index: null, formats: ['PDF'], readable: false, path: 'Author/Book (2)' },
  { id: 3, title: 'Gardening Basics', authors: ['Pat Example'], tags: ['Home'], series: null, series_index: null, formats: ['EPUB'], readable: true, path: 'Pat/Gardening (3)' },
];

const STORAGE_ENTRIES = [
  { path: '/Documents', name: 'Documents', is_dir: true, size: null, mtime: iso(-600), content_type: null, indexed: true },
  { path: '/Photos', name: 'Photos', is_dir: true, size: null, mtime: iso(-6000), content_type: null, indexed: false },
  { path: `/${UNBROKEN}.pdf`, name: `${UNBROKEN}.pdf`, is_dir: false, size: 2_400_000, mtime: iso(-60), content_type: 'application/pdf', indexed: true },
  { path: '/Household budget and bills for the whole of next year.xlsx', name: 'Household budget and bills for the whole of next year.xlsx',
    is_dir: false, size: 48_000, mtime: iso(-1200), content_type: 'application/vnd.ms-excel', indexed: false },
];

const LEARNINGS = {
  status: 'success', count: 2, items: [
    { id: 'l1', content: 'When a service will not start, read its last log lines before restarting it again and again.',
      metadata: { source: 'raven' }, created_at: Math.floor(NOW / 1000) - 3600, usage_count: 4, last_used_at: iso(-30),
      rule: 'Read the logs before restarting a service that will not start.', root_cause: `A missing setting: ${UNBROKEN}`,
      outcome: 'success', confidence: 0.9, applied_count: 3, supersedes: [] },
    { id: 'l2', content: 'Pin versions in requirements so a rebuild does not pick up a breaking release.',
      metadata: {}, created_at: Math.floor(NOW / 1000) - 86400, usage_count: 0, last_used_at: null,
      rule: 'Pin versions.', root_cause: 'An unpinned dependency.', outcome: 'partial', confidence: 0.6, applied_count: 0, supersedes: [] },
  ],
};

// ---------------------------------------------------------------- Workspaces

const workspace = (id: string, display_name: string, extra: Record<string, unknown> = {}) => ({
  id, display_name, local_path: `/workspaces/${id}`, resolved_path: `/workspaces/${id}`, available: true, nextcloud_path: null,
  repo_url: null, git_remote: null, default_branch: 'main', sync_mode: 'local', scope: 'private', capabilities: ['read', 'write'],
  owner_user: 'parent', is_default: false, auto_pull_enabled: false, auto_backup_enabled: false, webhook_token: null,
  quarantined: false, last_raven_mission_id: null, excludes: ['node_modules', '.git'], created_at: iso(-60 * 24 * 30),
  sync_owner: null, last_sync_at: null, last_sync_status: null, last_sync_error: null, ...extra,
});

const WORKSPACES = [
  workspace('family-website', 'The family website with a long display name for small phones', {
    repo_url: `https://git.example.test/family/${UNBROKEN}.git`, git_remote: 'origin', sync_mode: 'git', is_default: true,
    auto_pull_enabled: true, last_sync_at: iso(-15), last_sync_status: 'ok',
  }),
  workspace('school-projects', 'School projects', {
    nextcloud_path: '/Documents/School projects for this year and last year', sync_mode: 'nextcloud',
    last_sync_at: iso(-300), last_sync_status: 'error', last_sync_error: `Could not reach the folder: ${UNBROKEN}`,
  }),
  workspace('scratch', 'Scratch', { quarantined: true }),
];

const WORKSPACE_FILES = {
  status: 'success', relative_path: '.', truncated: false, entries: [
    { path: 'src', name: 'src', is_dir: true, size: null },
    { path: 'images/hero-banner-for-the-front-page.png', name: 'hero-banner-for-the-front-page.png', is_dir: false, size: 120_000 },
    { path: `exports/${UNBROKEN}.pdf`, name: `${UNBROKEN}.pdf`, is_dir: false, size: 3_400_000 },
    { path: 'audio/welcome.mp3', name: 'welcome.mp3', is_dir: false, size: 800_000 },
  ],
};

// ---------------------------------------------------------------- Artifacts

const artifact = (id: string, kind: string, name: string, extra: Record<string, unknown> = {}) => ({
  id, owner_user: 'parent', workspace_id: 'family-website', kind, name, mime_type: kind === 'image' ? 'image/png' : 'audio/mpeg',
  size: 4_200_000, storage_backend: 'local', sha256: null, generator: 'tts', metadata: { voice: 'Narrator' },
  created_at: iso(-600), updated_at: iso(-600), deleted_at: null, is_attached: true, download_url: null, ...extra,
});

const ARTIFACTS = [
  artifact('art-1', 'tts_audio', 'Morning briefing read aloud for the whole family on a school day.mp3'),
  artifact('art-2', 'image', `${UNBROKEN}.png`, { generator: 'image', metadata: { prompt: 'A lighthouse at dawn, painted in watercolour' } }),
  artifact('art-3', 'document', 'Report.pdf', { mime_type: 'application/pdf', is_attached: false, workspace_id: null }),
  artifact('art-4', 'video', 'Clip.webm', { mime_type: 'video/webm', deleted_at: iso(-60) }),
];

const STORAGE_USAGE = {
  user: 'parent', used: 410 * 1024 * 1024, quota: 500 * 1024 * 1024, free: 20 * 1024 ** 3, count: ARTIFACTS.length,
  by_kind: { tts_audio: 300 * 1024 * 1024, image: 100 * 1024 * 1024, document: 10 * 1024 * 1024 }, warn: true,
};

// ---------------------------------------------------------------- Docs

const DOCS = [
  { name: 'README', title: 'Read me first' },
  { name: 'ARCHITECTURE', title: 'How the services fit together, from the gateway to the media players' },
  { name: 'SETUP', title: UNBROKEN },
];

const DOC_BODY = [
  '# How the services fit together',
  '',
  'A made-up document for the layout check. It has headings, lists, a table and a code block, as the real docs do.',
  '',
  '## Services',
  '',
  ...Array.from({ length: 8 }, (_, i) => `- Service ${i + 1}: a line of text describing what this made-up service does and why.`),
  '',
  '| Service | Port | What it does |',
  '|---|---|---|',
  '| gateway | 8000 | Answers the app and routes every call to the service behind it |',
  `| ${UNBROKEN} | 8001 | A service with a long name |`,
  '',
  '```bash',
  `docker compose up -d --build gateway && docker compose logs -f gateway --since 10m | grep ${UNBROKEN}`,
  '```',
  '',
  ...Array.from({ length: 6 }, (_, i) => `Paragraph ${i + 1}. Placeholder words so the document is taller than the screen it is read on.`),
].join('\n');

// ---------------------------------------------------------------- Lab

const mission = (id: number, status: string, extra: Record<string, unknown> = {}) => ({
  id, mission_type: 'user_task', priority: 2, target_container: null, error_summary: null,
  proposed_mission: 'Look through the gateway logs for repeated timeouts and suggest a fix for each one found',
  coding_model: 'made-up-coder:7b', status, progress: status === 'running' ? 40 : status === 'completed' ? 100 : 0,
  scheduled_for: null, created_at: iso(-120), queued_at: iso(-119), started_at: status === 'pending' ? null : iso(-118),
  completed_at: status === 'completed' ? iso(-60) : null, duration: status === 'completed' ? 3600 : null,
  output_log: `Step 1: reading logs\nStep 2: ${UNBROKEN}`, result: status === 'completed' ? 'Found two timeouts and fixed both.' : null,
  user_id: 1, workspace_id: 'family-website', last_llm_reply: null, ...extra,
});

const MISSIONS = [
  mission(41, 'running'),
  mission(40, 'completed', { proposed_mission: UNBROKEN }),
  mission(39, 'failed', { error_summary: 'The container did not answer within the time allowed for the mission to run.' }),
  mission(38, 'pending'),
];

// ---------------------------------------------------------------- Media

function mediaEvents(): string {
  return ['retry: 3000', '', 'event: snapshot', `data: ${JSON.stringify({ type: 'snapshot', players: [] })}`, '',
    'event: heartbeat', 'data: {"type":"heartbeat"}', ''].join('\n');
}

function silentWav(frames = 4000): Buffer {
  const buffer = Buffer.alloc(44 + frames);
  buffer.write('RIFF', 0);
  buffer.writeUInt32LE(36 + frames, 4);
  buffer.write('WAVE', 8);
  buffer.write('fmt ', 12);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(8000, 24);
  buffer.writeUInt32LE(8000, 28);
  buffer.writeUInt16LE(1, 32);
  buffer.writeUInt16LE(8, 34);
  buffer.write('data', 36);
  buffer.writeUInt32LE(frames, 40);
  buffer.fill(0x80, 44);
  return buffer;
}

const ok = (message = '', detail: Record<string, unknown> = {}) => ({ status: 'SUCCESS', message, service: 'test', detail });

/** Answer the library pages' calls; everything else falls back to the mocks registered before. */
export async function mockLibraryPages(page: Page): Promise<void> {
  await page.route(isServerCall, async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    if (p === INTEGRATIONS_STATUS_PATH) return route.fulfill({ json: integrationsStatus() });
    const method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    let body: Record<string, unknown> = {};
    try { body = (req.postDataJSON() ?? {}) as Record<string, unknown>; } catch { body = {}; }

    // Pictures: recipe photos, cover art, artifact previews.
    if (p === '/api/media/recipe-image' || p.startsWith('/api/media/imageproxy') || /\/cover$/.test(p)
      || /^\/api\/artifacts\/[^/]+\/stream$/.test(p)) {
      return route.fulfill({ contentType: 'image/png', body: PNG_1X1 });
    }

    // Notes (and the recipe plan and shopping list, which are notes too).
    if (p === '/api/communication/notes/list') {
      const notes = body.previews ? NOTES : NOTES.map((n) => ({ title: n.title, path: n.path, size: n.size, modified: n.modified }));
      return json(ok('Notes listed', { notes, directories: ['Notes'] }));
    }
    if (p === '/api/communication/notes/meta') return json(ok('Notes index ready.', { meta: NOTES_META, path: 'Notes/.jarvis-notes.json' }));
    if (p === '/api/communication/notes/read') {
      const want = String(body.path ?? '');
      if (want === 'Recipes/Meal plan.md') return json(ok(MEAL_PLAN, { etag: '"p1"' }));
      if (want === 'Recipes/Shopping list.md') return json(ok(SHOPPING, { etag: '"s1"' }));
      if (want.startsWith('Recipes/')) return json({ status: 'FAILURE', message: 'Not found', service: 'note_read' });
      const note = NOTES.find((n) => n.path === want || n.title === body.title) ?? NOTES[0];
      return json(ok(note.content, { etag: '"n1"' }));
    }
    if (p === '/api/communication/notes/versions') {
      return json(ok('', { versions: [
        { version: 'v3', modified: iso(-60), size: 2200 }, { version: 'v2', modified: iso(-60 * 24), size: 2000 },
      ] }));
    }
    if (p === '/api/communication/notes/share') {
      return json(ok('', { shares: [{ id: 'sh1', share_with: 'sam', name: 'Sam', can_edit: false }],
        people: [{ id: 'sam', name: 'Sam' }, { id: 'robin', name: 'Robin With A Long Display Name For Small Phones' }] }));
    }

    // The family's places, for a note's "remind me when I get there".
    if (p === '/api/geo/zones') {
      const zone = (entity_id: string, friendly_name: string, lon: number, lat: number) => ({
        type: 'Feature', geometry: { type: 'Point', coordinates: [lon, lat] }, properties: { entity_id, friendly_name, radius: 100 },
      });
      return json({ type: 'FeatureCollection', features: [zone('zone.home', 'Home', -111.9, 33.4),
        zone('zone.school', 'The elementary school on the far side of town', -111.8, 33.5)] });
    }

    // Recipes.
    if (p === '/api/recipes/catalog') return json({ recipes: RECIPES, me: 'parent', is_admin: true });
    if (p === '/api/recipes/cookbook/suggestions') return json(ok('', { suggestions: [SUGGESTION] }));
    if (p === '/api/recipes/cookbook/personal') return json(ok('', { titles: ['Old family soup'] }));
    if (p === '/api/recipes/cookbook/read') {
      const r = RECIPES.find((x) => x.path === body.path) ?? RECIPES[0];
      return json(ok(RECIPE_BODY, { path: r.path, etag: '"r1"', can_change: r.can_change, owner_label: r.owner_label, image: r.image }));
    }
    if (p === '/api/recipes/sources') return json(ok('', { sources: [] }));
    if (p === '/api/integrations/skylight/meals') {
      return json({ status: 'SUCCESS', configured: true, categories: [{ id: 'c1', label: 'Dinner', color: '#f97316' }] });
    }

    // Media (the shared media fixtures).
    if (p === '/api/media/home') return json(media('media-home'));
    if (p === '/api/media/audiobookshelf/last-played') return json(media('abs-last-played'));
    if (p === '/execute/media/status') return json(media('media-status'));
    if (p === '/api/entities') return json(media('entities'));
    if (p === '/api/media/events') return route.fulfill({ contentType: 'text/event-stream', body: mediaEvents() });
    if (p === '/api/media/ma-command') {
      return json({ status: 'SUCCESS', result: body.command === 'players/all' ? [] : [] });
    }
    if (p.startsWith('/api/media/search')) return json(media('media-search'));
    if (p.startsWith('/api/media/library/')) return json(media('media-library'));
    if (p.startsWith('/api/media/favorites')) return json(media('media-favorites'));
    if (p === '/api/media/item') {
      return json(media((url.searchParams.get('uri') ?? '').startsWith('abs://') ? 'media-item-book' : 'media-item'));
    }
    if (p.startsWith('/api/media/detail')) return json(media('media-detail'));
    if (p === '/api/media/abs/progress') return json(media('abs-progress'));
    if (p.startsWith('/api/media/podcast-episodes')) return json(media('podcast-episodes'));
    if (p === '/api/groups/media' || p.startsWith('/api/groups/media/')) return json(media('groups-media'));
    if (p === '/execute/media/play') return json(media('play'));
    if (p === '/execute/media/transport') return json(media('transport'));
    if (p === '/execute/media/state/sync') return json(media('state-sync'));
    if (p.startsWith('/api/media/stream/')) return route.fulfill({ contentType: 'audio/wav', body: silentWav() });

    // Knowledge.
    if (p === '/api/library/tags') return json({ tags: [{ name: 'Bible study', books: 12 }, { name: 'Family', books: 4 }, { name: 'Home', books: 2 }] });
    if (p === '/api/library/books') return json({ total: BOOKS.length, books: BOOKS });
    // The book catalogues "Add a book" fills details from (Accounts & Integrations): Open Library only.
    if (p === '/api/library/catalogues') {
      return json({ order: ['google_books', 'open_library'], sources: {
        google_books: { name: 'Google Books', enabled: false, enabled_from: 'default', ready: false, key_set: false, key_source: 'absent',
          reason: 'Google Books: not set up (add an API key in Settings > Accounts & Integrations)' },
        open_library: { name: 'Open Library', enabled: true, enabled_from: 'default', ready: true, reason: '', contact_email: '' },
      } });
    }
    if (p === '/api/storage/stats') {
      return json({ total_chunks: 48210, total_documents: 1312, last_indexed: iso(-90), providers: ['nextcloud'],
        breakdown: { documents: { chunks: 40000, documents: 1200 }, notes: { chunks: 8210, documents: 112 } }, status: 'ok' });
    }
    if (p === '/api/storage/status') {
      return json({ status: 'ok', indexer: 'idle', checkpointed_files: 1312,
        crawl: { active: false, kind: 'nextcloud', path: '/', phase: 'done', done: 1312, total: 1312 } });
    }
    if (p === '/api/storage/list') return json({ entries: STORAGE_ENTRIES });
    if (p === '/api/storage/learning') return json(LEARNINGS);

    // Workspaces.
    if (p === '/api/workspaces' && method === 'GET') return json({ workspaces: WORKSPACES });
    if (p === '/api/workspaces/sessions') return json({ sessions: {} });
    if (p === '/api/workspaces/files/list') return json(WORKSPACE_FILES);

    // Artifacts.
    if (p === '/api/storage/usage') return json(STORAGE_USAGE);
    if (p === '/api/artifacts' && method === 'GET') return json({ artifacts: ARTIFACTS, total: ARTIFACTS.length, limit: 50, offset: 0 });
    const download = /^\/api\/artifacts\/([^/]+)\/download$/.exec(p);
    if (download) {
      const a = ARTIFACTS.find((x) => x.id === download[1]) ?? ARTIFACTS[0];
      return json({ url: `/api/artifacts/${a.id}/stream?token=t`, expires_in: 300, name: a.name });
    }

    // Docs.
    if (p === '/api/docs') return json({ docs: DOCS });
    if (p.startsWith('/api/docs/')) return json({ content: DOC_BODY });

    // Lab.
    if (p === '/api/raven/missions' && method === 'GET') return json(MISSIONS);

    return route.fallback();
  });
}
