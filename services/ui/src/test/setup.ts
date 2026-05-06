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
  http.get('/api/auth/discover', () => HttpResponse.json(discoveredUsers)),
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
  http.get('/health/ready', () => HttpResponse.json({
    status: 'READY',
    services: {
      gateway: 'OK',
      identity: 'OK',
      execution: 'OK',
      workspace_runtime: 'OK',
    },
  })),
  http.get('/api/workspaces', () => HttpResponse.json([
    { id: 'ws1', name: 'SharedLLM', resolved_path: '/workspace/SharedLLM', available: true, scope: 'system' },
  ])),
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
  http.get('/api/docs/:docName', () => HttpResponse.json({ content: '# Docs\n\nUseful documentation.' })),
  http.post('/api/auth/test-connection', () => HttpResponse.json({ status: 'SUCCESS', message: 'Connected' })),
  http.post('/api/users/me/enroll', () => HttpResponse.json({ status: 'SUCCESS', message: 'Enrolled' })),
);

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));

beforeEach(() => {
  resetMockState();
  localStorage.clear();
  localStorage.setItem('jarvis_api_key', 'test-token');
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
});

afterAll(() => server.close());
