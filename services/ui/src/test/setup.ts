import '@testing-library/jest-dom';
import { afterAll, afterEach, beforeAll, beforeEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';

const defaultUser = {
  id: 1,
  username: 'default',
  display_name: 'Shared/Default User',
  full_name: 'Shared/Default User',
  role: 'admin',
  is_admin: true,
  is_system_default: true,
  nextcloud_url: 'https://cloud.example.com',
  nextcloud_user: 'default',
  ha_url: 'https://ha.example.com',
  voice_fingerprint: null,
};

let users: Array<Record<string, unknown>> = [];
let discoveredUsers: Array<Record<string, unknown>> = [];
let devices: Array<Record<string, unknown>> = [];
let settings: Array<Record<string, unknown>> = [];
let timers: Array<Record<string, unknown>> = [];
let apiKeys: Array<Record<string, unknown>> = [];
let logs: Array<Record<string, unknown>> = [];
let talkConversations: Array<Record<string, unknown>> = [];
let talkMessages: Record<string, Array<Record<string, unknown>>> = {};
let widgetSettings: Array<Record<string, unknown>> = [];
let quickAssistantEnabled = false;

const resetMockState = () => {
  users = [structuredClone(defaultUser)];
  discoveredUsers = [
    { username: 'jeremiah', source: 'Home Assistant', display_name: 'Jeremiah' },
    { username: 'michele', source: 'Home Assistant', display_name: 'Michele' },
  ];
  devices = [
    { id: 1, device_id: 'media_player.office_speaker', user_id: 1, username: 'default' },
    { id: 2, device_id: 'media_player.living_room_tv', user_id: 1, username: 'default' },
  ];
  settings = [
    { key: 'system_name', value: 'Jarvis OS', description: 'Displayed system name' },
    { key: 'system_log_level', value: 'INFO', description: 'Global log level' },
  ];
  timers = [
    { id: 'timer-1', type: 'timer', title: 'Kitchen Timer', expires_at: '2026-05-06T18:00:00', active: true },
  ];
  apiKeys = [
    { id: 1, label: 'OpenWebUI', prefix: 'sk-jarvis', created_at: '2026-05-06T01:00:00' },
  ];
  logs = [
    { id: 1, timestamp: '2026-05-06T09:00:00Z', service: 'gateway', level: 'INFO', message: 'Gateway ready', context: null },
    { id: 2, timestamp: '2026-05-06T09:01:00Z', service: 'identity', level: 'INFO', message: 'Identity ready', context: null },
  ];
  talkConversations = [
    {
      id: 1,
      token: 'room-alpha',
      display_name: 'Family',
      name: 'Family',
      description: 'Family coordination',
      unread_messages: 1,
      last_activity: 1715000000,
      last_message: 'Dinner at 6.',
    },
    {
      id: 2,
      token: 'room-work',
      display_name: 'Ops',
      name: 'Ops',
      description: 'Operations',
      unread_messages: 0,
      last_activity: 1715001000,
      last_message: 'Deploy complete.',
    },
  ];
  talkMessages = {
    'room-alpha': [
      {
        id: 101,
        token: 'room-alpha',
        actor_type: 'users',
        actor_id: 'michele',
        actor_display_name: 'Michele',
        timestamp: 1715000000,
        message_type: 'comment',
        system_message: '',
        message: 'Dinner at 6.',
        is_replyable: true,
      },
    ],
    'room-work': [
      {
        id: 201,
        token: 'room-work',
        actor_type: 'users',
        actor_id: 'default',
        actor_display_name: 'Shared/Default User',
        timestamp: 1715001000,
        message_type: 'comment',
        system_message: '',
        message: 'Deploy complete.',
        is_replyable: true,
      },
    ],
  };
  widgetSettings = [
    {
      widget_key: 'energy_insights',
      visibility: 'visible',
      order_index: 0,
      size: 'medium',
      is_pinned: false,
      sort_mode: null,
      pinned_devices: [],
      config: {},
      updated_at: Date.now(),
    },
    {
      widget_key: 'ambient_timer',
      visibility: 'visible',
      order_index: 1,
      size: 'small',
      is_pinned: false,
      sort_mode: null,
      pinned_devices: [],
      config: {},
      updated_at: Date.now(),
    },
    {
      widget_key: 'quick_notes',
      visibility: 'visible',
      order_index: 2,
      size: 'medium',
      is_pinned: false,
      sort_mode: null,
      pinned_devices: [],
      config: {},
      updated_at: Date.now(),
    },
    {
      widget_key: 'active_media',
      visibility: 'visible',
      order_index: 3,
      size: 'wide',
      is_pinned: false,
      sort_mode: null,
      pinned_devices: [],
      config: {},
      updated_at: Date.now(),
    },
    {
      widget_key: 'chores_progress',
      visibility: 'visible',
      order_index: 4,
      size: 'tall',
      is_pinned: false,
      sort_mode: null,
      pinned_devices: [],
      config: {},
      updated_at: Date.now(),
    },
    {
      widget_key: 'upcoming_events',
      visibility: 'visible',
      order_index: 5,
      size: 'wide',
      is_pinned: false,
      sort_mode: null,
      pinned_devices: [],
      config: {},
      updated_at: Date.now(),
    },
    {
      widget_key: 'device_control',
      visibility: 'visible',
      order_index: 6,
      size: 'tall',
      is_pinned: false,
      sort_mode: 'most_used',
      pinned_devices: [],
      config: {},
      updated_at: Date.now(),
    }
  ];
  quickAssistantEnabled = false;
};

export const server = setupServer(
  http.post('/api/auth/login', async () => HttpResponse.json({ api_key: 'test-token', username: 'default', is_admin: true })),
  http.get('/api/users/me', () => HttpResponse.json(users[0])),
  http.get('/api/users', () => HttpResponse.json(users)),
  http.post('/api/users', async ({ request }) => {
    const body = await request.json() as Record<string, unknown>;
    const created = {
      id: users.length + 1,
      display_name: body.display_name || body.full_name || body.username,
      is_system_default: false,
      ...body,
    };
    users.push(created);
    discoveredUsers = discoveredUsers.filter((user) => user.username !== body.username);
    return HttpResponse.json(created);
  }),
  http.patch('/api/users/:username', async ({ params, request }) => {
    const body = await request.json() as Record<string, unknown>;
    const username = String(params.username);
    users = users.map((user) => user.username === username ? { ...user, ...body } : user);
    return HttpResponse.json(users.find((user) => user.username === username));
  }),
  http.delete('/api/users/:username', ({ params }) => {
    const username = String(params.username);
    users = users.filter((user) => user.username !== username);
    return HttpResponse.json({ status: 'SUCCESS' });
  }),
  http.get('/api/auth/discover', () => {
    console.log('[MSW] /api/auth/discover called, discoveredUsers:', JSON.stringify(discoveredUsers));
    return HttpResponse.json({
      users: discoveredUsers,
      warnings: [],
      errors: [],
    });
  }),
  http.get('/api/users/devices', () => HttpResponse.json(devices)),
  http.post('/api/users/devices', async ({ request }) => {
    const body = await request.json() as Record<string, unknown>;
    const existing = devices.find((device) => device.device_id === body.device_id);
    const user = users.find((entry) => entry.username === body.username);
    const saved = {
      id: existing?.id || devices.length + 1,
      device_id: body.device_id,
      user_id: user?.id || 1,
      username: body.username,
    };
    devices = existing
      ? devices.map((device) => device.device_id === body.device_id ? saved : device)
      : [...devices, saved];
    return HttpResponse.json(saved);
  }),
  http.delete('/api/devices/:deviceId', ({ params }) => {
    const deviceId = decodeURIComponent(String(params.deviceId));
    devices = devices.filter((device) => device.device_id !== deviceId);
    return HttpResponse.json({ status: 'SUCCESS' });
  }),
  http.post('/api/discovery/sync', () => HttpResponse.json({ status: 'SUCCESS', entities_count: devices.length })),
  http.get('/api/settings', () => HttpResponse.json(settings)),
  http.patch('/api/settings/:key', async ({ params, request }) => {
    const body = await request.json() as Record<string, unknown>;
    const key = String(params.key);
    const existing = settings.find((setting) => setting.key === key);
    const saved = { key, value: body.value, description: existing?.description || null };
    settings = existing
      ? settings.map((setting) => setting.key === key ? saved : setting)
      : [...settings, saved];
    return HttpResponse.json(saved);
  }),
  http.post('/api/settings', async ({ request }) => {
    const body = await request.json() as Record<string, unknown>;
    for (const [key, value] of Object.entries(body)) {
      const existing = settings.find((setting) => setting.key === key);
      const saved = { key, value: String(value ?? ''), description: existing?.description || null };
      settings = existing
        ? settings.map((setting) => setting.key === key ? saved : setting)
        : [...settings, saved];
    }
    return HttpResponse.json({ status: 'SUCCESS' });
  }),
  http.get('/health/ready', () => HttpResponse.json({
    status: 'READY',
    services: {
      gateway: 'OK',
      identity: 'OK',
      execution: 'OK',
      workspace_runtime: 'OK',
    },
  })),
  http.get('/api/workspaces', () => HttpResponse.json({
    status: 'SUCCESS',
    workspaces: [
      { id: 'ws1', display_name: 'SharedLLM', local_path: 'SharedLLM', resolved_path: '/workspace/SharedLLM', available: true, scope: 'system', capabilities: [], sync_mode: 'local_git_authoritative', auto_pull_enabled: false },
    ],
  })),
  http.get('/api/logs', () => HttpResponse.json(logs)),
  http.post('/api/admin/tests/smoke', () => HttpResponse.json({
    status: 'SUCCESS',
    passed: true,
    results: 'PASS: health\nPASS: discovery\nPASS: smoke',
  })),
  http.get('/api/users/me/keys', () => HttpResponse.json(apiKeys)),
  http.post('/api/users/me/keys', async ({ request }) => {
    const body = await request.json() as Record<string, unknown>;
    const key = {
      id: apiKeys.length + 1,
      label: body.label || 'New Key',
      prefix: 'sk-test',
      key: 'sk-test-generated-key',
      created_at: '2026-05-06T02:00:00',
    };
    apiKeys.push(key);
    return HttpResponse.json(key);
  }),
  http.delete('/api/users/me/keys/:keyId', ({ params }) => {
    apiKeys = apiKeys.filter((key) => String(key.id) !== String(params.keyId));
    return HttpResponse.json({ success: true });
  }),
  http.get('/api/communication/timers', () => HttpResponse.json(timers)),
  http.post('/api/communication/timers', async ({ request }) => {
    const body = await request.json() as Record<string, unknown>;
    const timer = {
      id: `timer-${timers.length + 1}`,
      type: body.type || 'timer',
      title: body.title || 'Untitled',
      expires_at: '2026-05-06T19:00:00',
      active: true,
    };
    timers = [...timers, timer];
    return HttpResponse.json({ status: 'SUCCESS', message: `Set timer '${timer.title}'.`, service: 'timer_add' });
  }),
  http.delete('/api/communication/timers', async ({ request }) => {
    const body = await request.json() as Record<string, unknown>;
    timers = timers.filter((timer) => timer.title !== body.title);
    return HttpResponse.json({ status: 'SUCCESS', message: 'Deleted timer.', service: 'timer_delete' });
  }),
  http.get('/api/communication/calendar/calendars', () => HttpResponse.json({
    status: 'SUCCESS',
    message: 'Available Calendars:\n- Family\n- Work',
    service: 'calendar_list',
  })),
  http.get('/api/communication/calendar/events', () => HttpResponse.json({
    status: 'SUCCESS',
    message: 'Upcoming Events:\n- [2026-05-06 06:00 PM] Family Sync (Family)',
    service: 'calendar_read',
  })),
  http.post('/api/communication/calendar/events', () => HttpResponse.json({
    status: 'SUCCESS',
    message: 'Added event.',
    service: 'calendar_add',
  })),
  http.post('/api/communication/notes/create', () => HttpResponse.json({
    status: 'SUCCESS',
    message: 'Note created.',
    service: 'note_create',
  })),
  http.post('/api/communication/notes/read', () => HttpResponse.json({
    status: 'SUCCESS',
    message: '# Shared Checklist\n- [ ] Pick up groceries',
    service: 'note_read',
  })),
  http.post('/api/communication/notes/append', () => HttpResponse.json({
    status: 'SUCCESS',
    message: 'Note appended.',
    service: 'note_append',
  })),
  http.post('/api/communication/notes/delete', () => HttpResponse.json({
    status: 'SUCCESS',
    message: 'Note deleted.',
    service: 'note_delete',
  })),
  http.post('/api/communication/announcements', () => HttpResponse.json({
    status: 'SUCCESS',
    message: 'Announcement sent.',
    service: 'announce',
  })),
  http.get('/api/communication/talk/conversations', () => HttpResponse.json({
    status: 'SUCCESS',
    message: `Loaded ${talkConversations.length} conversation(s).`,
    service: 'talk_list',
    detail: { conversations: talkConversations },
  })),
  http.post('/api/communication/talk/conversations/open', async ({ request }) => {
    const body = await request.json() as Record<string, unknown>;
    let conversation = talkConversations.find((item) => item.token === body.token);
    if (!conversation && body.target_user) {
      conversation = {
        id: talkConversations.length + 1,
        token: `dm-${String(body.target_user)}`,
        display_name: `DM ${String(body.target_user)}`,
        name: '',
        description: '',
        unread_messages: 0,
        last_activity: 1715002000,
        last_message: '',
      };
      talkConversations = [conversation, ...talkConversations];
      talkMessages[String(conversation.token)] = [];
    }
    return HttpResponse.json({
      status: 'SUCCESS',
      message: 'Opened conversation.',
      service: 'talk_open',
      detail: { conversation },
    });
  }),
  http.get('/api/communication/talk/messages', ({ request }) => {
    const url = new URL(request.url);
    const token = url.searchParams.get('token') || '';
    return HttpResponse.json({
      status: 'SUCCESS',
      message: `Loaded ${(talkMessages[token] || []).length} message(s).`,
      service: 'talk_messages',
      detail: { messages: talkMessages[token] || [] },
    });
  }),
  http.post('/api/communication/talk/messages', async ({ request }) => {
    const body = await request.json() as Record<string, unknown>;
    const token = String(body.token);
    const entry = {
      id: Date.now(),
      token,
      actor_type: 'users',
      actor_id: 'default',
      actor_display_name: 'Shared/Default User',
      timestamp: 1715003000,
      message_type: 'comment',
      system_message: '',
      message: String(body.message || ''),
      is_replyable: true,
    };
    talkMessages[token] = [...(talkMessages[token] || []), entry];
    talkConversations = talkConversations.map((conversation) =>
      conversation.token === token
        ? { ...conversation, last_message: entry.message, last_activity: entry.timestamp }
        : conversation,
    );
    return HttpResponse.json({
      status: 'SUCCESS',
      message: 'Chat message sent.',
      service: 'talk_send',
      detail: { message_record: entry },
    });
  }),
  http.post('/api/communication/talk/voice', async ({ request }) => {
    const body = await request.json() as Record<string, unknown>;
    const token = String(body.token);
    const caption = String(body.caption || 'Voice message');
    const entry = {
      id: Date.now() + 1,
      token,
      actor_type: 'users',
      actor_id: 'default',
      actor_display_name: 'Shared/Default User',
      timestamp: 1715004000,
      message_type: 'voice-message',
      system_message: '',
      message: caption,
      is_replyable: true,
    };
    talkMessages[token] = [...(talkMessages[token] || []), entry];
    talkConversations = talkConversations.map((conversation) =>
      conversation.token === token
        ? { ...conversation, last_message: caption, last_activity: entry.timestamp }
        : conversation,
    );
    return HttpResponse.json({
      status: 'SUCCESS',
      message: 'Voice message sent to Nextcloud Talk.',
      service: 'talk_send_voice',
      detail: { path: '/Talk Uploads/voice-message.webm' },
    });
  }),
  http.get('/api/docs/:docName', () => HttpResponse.json({ content: '# Docs\n\nUseful documentation.' })),
  http.post('/api/auth/test-connection', () => HttpResponse.json({ status: 'SUCCESS', message: 'Connected' })),
  http.post('/api/users/me/enroll', () => HttpResponse.json({ status: 'SUCCESS', message: 'Enrolled' })),
  http.post('/api/auth/import/nextcloud', () => HttpResponse.json({ status: 'SUCCESS', message: 'Users imported from Nextcloud' })),
  http.post('/api/storage/list', () => HttpResponse.json({
    status: 'SUCCESS',
    entries: [
      { path: '/Notes', name: 'Notes', is_dir: true, size: null, indexed: false },
      { path: '/test.txt', name: 'test.txt', is_dir: false, size: 1024, indexed: true },
    ]
  })),
  http.post('/api/storage/index', () => HttpResponse.json({
    status: 'ACCEPTED',
    message: 'Indexing started'
  })),
  http.get('/api/storage/stats', () => HttpResponse.json({
    total_chunks: 1234,
    total_documents: 42,
    last_indexed: '2026-05-06T10:00:00Z',
    providers: ['nextcloud'],
    breakdown: { home_assistant: { chunks: 500, documents: 200 }, notes: { chunks: 200, documents: 50 } }
  })),
  http.get('/api/storage/collection/:name', () => HttpResponse.json({ items: [] })),
  http.get('/api/entities', () => HttpResponse.json([
    { entity_id: 'media_player.kitchen_echo', domain: 'media_player', friendly_name: 'Kitchen Echo' },
    { entity_id: 'light.living_room', domain: 'light', friendly_name: 'Living Room Light' },
    { entity_id: 'media_player.living_room_tv', domain: 'media_player', friendly_name: 'Living Room TV' },
  ])),

  // Media groups / light clusters / light patterns
  http.get('/api/groups/media', () => HttpResponse.json({ groups: [] })),
  http.post('/api/groups/media', () => HttpResponse.json({ status: 'SUCCESS', message: 'Group created' })),
  http.delete('/api/groups/media/:name', () => HttpResponse.json({ status: 'SUCCESS', message: 'Group deleted' })),
  http.get('/api/groups/lights', () => HttpResponse.json({ clusters: [] })),
  http.post('/api/groups/lights', () => HttpResponse.json({ status: 'SUCCESS', message: 'Cluster created' })),
  http.delete('/api/groups/lights/:name', () => HttpResponse.json({ status: 'SUCCESS', message: 'Cluster deleted' })),
  http.get('/api/groups/patterns', () => HttpResponse.json({ patterns: [] })),
  http.post('/api/groups/patterns', () => HttpResponse.json({ status: 'SUCCESS', message: 'Pattern created' })),
  http.delete('/api/groups/patterns/:name', () => HttpResponse.json({ status: 'SUCCESS', message: 'Pattern deleted' })),
  http.post('/execute/groups/lights', () => HttpResponse.json({ status: 'SUCCESS', message: 'Pattern executed' })),

  // Telemetry
  http.get('/api/telemetry/enroll', () => HttpResponse.json({ enrollments: [] })),
  http.post('/api/telemetry/enroll', () => HttpResponse.json({ status: 'SUCCESS', message: 'Device enrolled' })),
  http.delete('/api/telemetry/enroll/:entityId', () => HttpResponse.json({ status: 'SUCCESS', message: 'Device unenrolled' })),
  http.post('/api/telemetry/analyze', () => HttpResponse.json({ status: 'SUCCESS', message: 'Analysis queued' })),

  // Intercom
  http.get('/api/intercom/sessions', () => HttpResponse.json([])),
  http.post('/api/intercom/sessions', () => HttpResponse.json({ session_id: 'mock-session-1', status: 'active' })),
  http.delete('/api/intercom/sessions/:sessionId', () => HttpResponse.json({ status: 'SUCCESS', message: 'Session ended' })),
  http.post('/api/intercom/broadcast', () => HttpResponse.json({ status: 'SUCCESS', targets_count: 0 })),
  http.post('/api/intercom/announce', () => HttpResponse.json({ status: 'SUCCESS', targets_count: 0 })),
  http.get('/api/intercom/config', () => HttpResponse.json({
    default_tts_engine: 'kokoro',
    default_voice: 'af_heart',
    default_volume: 0.8,
    enable_espresense_routing: true,
  })),

  // Raven endpoints
  http.get('/api/admin/raven/config', () => HttpResponse.json({
    raven_suspended: false,
    raven_scan_interval: 300,
    raven_error_threshold: 5,
    active_coding_model: 'qwen2.5-coder:7b',
    system_default_tts_voice: 'af_heart',
    system_default_tts_engine: 'kokoro',
  })),
  http.patch('/api/admin/raven/config', () => HttpResponse.json({ status: 'success' })),
  http.get('/api/admin/raven/tts/voices', () => HttpResponse.json({ status: 'success', voices: ['af_heart', 'am_adam'] })),
  http.get('/api/admin/raven/queue', () => HttpResponse.json([])),
  http.post('/api/admin/raven/queue/:id/execute', () => HttpResponse.json({ status: 'SUCCESS', message: 'Dispatched' })),
  http.get('/api/raven/missions', () => HttpResponse.json([])),
  http.post('/api/raven/missions', () => HttpResponse.json({ status: 'SUCCESS', mission: { id: 1, mission_type: 'user_task', status: 'pending', progress: 0, created_at: '2026-05-15T00:00:00Z', proposed_mission: 'test' } })),
  http.post('/api/raven/missions/:id/kill', () => HttpResponse.json({ status: 'SUCCESS', message: 'Killed' })),

  // Gateway config & models
  http.get('/api/config', () => HttpResponse.json({ config: { assistant_model: 'qwen3:8b', coding_model: 'qwen2.5-coder:7b', librarian_model: 'qwen3:8b' } })),
  http.post('/api/config', () => HttpResponse.json({ config: { assistant_model: 'qwen3:8b', coding_model: 'qwen2.5-coder:7b', librarian_model: 'qwen3:8b' } })),
  http.get('/api/config/models', () => HttpResponse.json({ models: ['qwen3:8b', 'qwen2.5-coder:7b'] })),

  // Widgets Settings
  http.get('/api/widgets/settings', () => HttpResponse.json({
    widgets: widgetSettings,
    quick_assistant_enabled: quickAssistantEnabled,
  })),
  http.patch('/api/widgets/settings/:widgetKey', async ({ params, request }) => {
    const body = await request.json() as Record<string, unknown>;
    const widgetKey = String(params.widgetKey);
    widgetSettings = widgetSettings.map((w) =>
      w.widget_key === widgetKey ? { ...w, ...body } : w
    );
    return HttpResponse.json({ status: 'SUCCESS' });
  }),

  // Global search (RAG)
  http.get('/api/search', () => HttpResponse.json({
    answer: 'Mocked RAG search result answer',
    files: [
      { name: 'document.txt', path: '/docs/document.txt' },
      { name: 'notes.md', path: '/Notes/notes.md' }
    ]
  })),

  // HA Entities and services
  http.post('/execute/entity/search', () => HttpResponse.json({
    status: 'SUCCESS',
    result: [
      { entity_id: 'light.living_room', friendly_name: 'Living Room Light', state: 'off', domain: 'light', area_id: 'living_room' },
      { entity_id: 'switch.coffee_maker', friendly_name: 'Coffee Maker', state: 'on', domain: 'switch', area_id: 'kitchen' },
      { entity_id: 'media_player.office_speaker', friendly_name: 'Office Speaker', state: 'playing', domain: 'media_player', area_id: 'office' }
    ]
  })),
  http.post('/execute/ha_service', () => HttpResponse.json({
    status: 'SUCCESS',
    message: 'Service called successfully'
  })),

  // Media controls
  http.post('/execute/media/status', () => HttpResponse.json({
    status: 'SUCCESS',
    data: {
      title: 'Mock Song Title',
      artist: 'Mock Artist',
      device_name: 'Office Speaker',
      state: 'playing',
      volume_level: 0.5,
    }
  })),
  http.post('/execute/media/transport', () => HttpResponse.json({
    status: 'SUCCESS',
    message: 'Transport command executed'
  })),

  // Telemetry summaries
  http.get('/api/telemetry/summary/:entityId', ({ params }) => HttpResponse.json({
    entity_id: String(params.entityId),
    summary: {
      current_power_w: 120,
      peak_power_w: 350,
      avg_power_w: 150,
      availability_pct: 100,
      total_activations: 15,
      data_points: [
        { recorded_at: Math.floor(Date.now() / 1000) - 3600, power_w: 100 },
        { recorded_at: Math.floor(Date.now() / 1000), power_w: 120 }
      ]
    }
  })),

  // Media Playlists and Audiobookshelf mocks for Media.tsx page
  http.get('/api/media/music-assistant/playlists', () => HttpResponse.json({
    status: 'SUCCESS',
    playlists: [
      { name: 'Rock Classics', items: 25, uri: 'ma://playlist/rock' },
      { name: 'Chill Vibes', items: 10, uri: 'ma://playlist/chill' }
    ]
  })),
  http.get('/api/media/music-assistant/recent', () => HttpResponse.json({
    status: 'SUCCESS',
    recent: [
      { name: 'Recent Rock Song', artist: 'Recent Rock Artist', uri: 'ma://track/recent1', last_played: '2026-05-06T11:00:00Z' }
    ]
  })),
  http.get('/api/media/audiobookshelf/libraries', () => HttpResponse.json({
    status: 'SUCCESS',
    libraries: [
      { id: 'lib-1', name: 'Audiobooks', media_type: 'audiobook' }
    ]
  })),
  http.get('/api/media/audiobookshelf/library/:libraryId', () => HttpResponse.json({
    status: 'SUCCESS',
    books: [
      { id: 'book-1', title: 'The Great Gatsby', author: 'F. Scott Fitzgerald' },
      { id: 'book-2', title: '1984', author: 'George Orwell' }
    ]
  })),
  http.get('/api/media/audiobookshelf/search', () => HttpResponse.json({
    status: 'SUCCESS',
    books: [
      { id: 'book-1', title: 'The Great Gatsby', author: 'F. Scott Fitzgerald' }
    ]
  })),
  http.get('/api/media/audiobookshelf/last-played', () => HttpResponse.json({
    status: 'SUCCESS',
    books: [
      { id: 'book-1', title: 'The Great Gatsby', author: 'F. Scott Fitzgerald', progress: 0.45, last_played: '2026-05-06T12:00:00Z', library_id: 'lib-1' }
    ]
  })),
  http.post('/execute/audiobookshelf', () => HttpResponse.json({
    status: 'SUCCESS',
    message: 'Audiobook play started'
  })),
  http.post('/execute/media/play', () => HttpResponse.json({
    status: 'SUCCESS',
    message: 'Media play started'
  })),
);

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));

beforeEach(() => {
  resetMockState();
  localStorage.clear();
  localStorage.setItem('jarvis_api_key', 'test-token');
  class MockMediaRecorder {
    static isTypeSupported() {
      return true;
    }
    ondataavailable: ((event: BlobEvent) => void) | null = null;
    onstop: (() => void) | null = null;
    state = 'inactive';
    constructor() {}
    start() {
      this.state = 'recording';
    }
    stop() {
      this.state = 'inactive';
      this.ondataavailable?.({ data: new Blob(['mock-audio'], { type: 'audio/webm' }) } as BlobEvent);
      this.onstop?.();
    }
  }
  Object.defineProperty(window, 'MediaRecorder', {
    writable: true,
    value: MockMediaRecorder,
  });
  Object.defineProperty(window.navigator, 'mediaDevices', {
    writable: true,
    value: {
      getUserMedia: async () => ({
        getTracks: () => [{ stop: () => undefined }],
      }),
    },
  });
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
});

afterAll(() => server.close());
