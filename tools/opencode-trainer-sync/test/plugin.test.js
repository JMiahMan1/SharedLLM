// Unit tests for the trainer-sync plugin: opt-in, arguments, queueing, logging.
// Run: node --test tools/opencode-trainer-sync/test
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

import plugin from '../index.js';
import { EXIT_QUEUED, flushArgs, resolveConfig, SyncQueue, syncArgs } from '../lib.js';

function tmpdir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'trainer-sync-'));
}

function options(extra = {}) {
  return {
    enabled: true,
    repo: '/code/SharedLLM',
    gateway: 'https://gateway.test',
    keyFile: '/keys/trainer-key',
    stateDir: tmpdir(),
    ...extra,
  };
}

test('it is off unless the options enable it', () => {
  assert.equal(resolveConfig({}, '/p').enabled, false);
  assert.equal(resolveConfig({ ...options(), enabled: false }, '/p').enabled, false);
});

test('enabled options still need the project to opt in', () => {
  const project = tmpdir();
  const off = resolveConfig(options(), project);
  assert.equal(off.enabled, false);
  assert.match(off.reason, /not in projects/);

  assert.equal(resolveConfig(options({ projects: [project] }), project).enabled, true);

  fs.mkdirSync(path.join(project, '.opencode'));
  fs.writeFileSync(path.join(project, '.opencode', 'trainer-sync.json'), JSON.stringify({ enabled: true }));
  assert.equal(resolveConfig(options(), project).enabled, true);
});

test('a project file can opt a listed project out', () => {
  const project = tmpdir();
  fs.mkdirSync(path.join(project, '.opencode'));
  fs.writeFileSync(path.join(project, '.opencode', 'trainer-sync.json'), JSON.stringify({ enabled: false }));
  assert.equal(resolveConfig(options({ projects: [project] }), project).enabled, false);
});

test('missing settings are an error that names them', () => {
  const project = tmpdir();
  const config = resolveConfig({ enabled: true, projects: [project] }, project);
  assert.equal(config.enabled, false);
  assert.equal(config.error, true);
  assert.match(config.reason, /repo/);
  assert.match(config.reason, /gateway/);
  assert.match(config.reason, /keyFile or keyEnv/);
  const unset = resolveConfig(options({ projects: [project], keyFile: undefined, keyEnv: 'NOPE_KEY' }), project, {});
  assert.match(unset.reason, /NOPE_KEY/);
});

test('the key is never an argument: only its file or variable name', () => {
  const project = tmpdir();
  const config = resolveConfig(options({ projects: [project], namesFile: '~/names.txt', keepIps: true }), project);
  const args = syncArgs(config, 'ses_1');
  assert.deepEqual(args.slice(1, 7), ['sync', '--source', 'opencode', '--session', 'ses_1', '--gateway']);
  assert.ok(args.includes('--key-file') && args.includes('/keys/trainer-key'));
  assert.ok(args.includes('--names-file') && args.includes(path.join(os.homedir(), 'names.txt')));
  assert.ok(args.includes('--keep-ips'));
  assert.ok(args[0].endsWith(path.join('scripts', 'collect_teacher_traces.py')));
  assert.ok(flushArgs(config).includes('flush'));
});

function harness(results) {
  const config = { ...resolveConfig(options({ projects: ['/p'] }), '/p'), debounceSeconds: 0, retryMinutes: 1 };
  const calls = [];
  const logs = [];
  const timers = [];
  const queue = new SyncQueue(config, {
    run: async (command, args) => {
      calls.push(args);
      return results.shift() ?? { code: 0, stdout: '{"tasks":0}', stderr: '' };
    },
    log: async (level, message) => logs.push({ level, message }),
    setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimer: () => {},
  });
  return { config, queue, calls, logs, timers };
}

test('a successful sync logs counts only', async () => {
  const { queue, logs } = harness([
    { code: 0, stdout: JSON.stringify({ tasks: 2, upload: { accepted: 2 }, by_scrub_status: { masked: 2 } }), stderr: '' },
  ]);
  await queue.enqueue('ses_1');
  assert.equal(logs[0].level, 'info');
  assert.match(logs[0].message, /2 task\(s\), accepted 2/);
});

test('a queued upload is reported as a warning and retried by flush', async () => {
  const { queue, logs, timers, calls } = harness([
    { code: EXIT_QUEUED, stdout: JSON.stringify({ tasks: 1, upload: { queued: 1, errors: ['the gateway did not answer'] } }), stderr: '' },
    { code: 0, stdout: '{"sent":1,"left":0}', stderr: '' },
  ]);
  await queue.enqueue('ses_2');
  assert.equal(logs[0].level, 'warn');
  assert.match(logs[0].message, /queued for retry.*did not answer/);
  assert.equal(timers.length, 1);
  await timers[0].fn();
  assert.ok(calls[1].includes('flush'));
});

test('a collector failure keeps the session and says so in the log', async () => {
  const { queue, logs, config, timers } = harness([{ code: 2, stdout: '', stderr: 'collect_teacher_traces: names file missing' }]);
  await queue.enqueue('ses_3');
  assert.equal(logs[0].level, 'error');
  assert.match(logs[0].message, /names file missing/);
  const pending = JSON.parse(fs.readFileSync(path.join(config.stateDir, 'plugin-pending.json'), 'utf8'));
  assert.deepEqual(pending, ['ses_3']);
  assert.equal(timers.length, 1);
});

test('the plugin listens for session.idle only when enabled', async () => {
  const logs = [];
  const ctx = {
    worktree: '/not/opted/in',
    client: { app: { log: async ({ body }) => logs.push(body) } },
  };
  const disabled = await plugin.server(ctx, options());
  assert.deepEqual(disabled, {});

  const project = tmpdir();
  const hooks = await plugin.server({ ...ctx, worktree: project }, options({ projects: [project], debounceSeconds: 3600 }));
  assert.equal(typeof hooks.event, 'function');
  assert.equal(logs.at(-1).service, 'trainer-sync');
  await hooks.event({ event: { type: 'message.updated', properties: {} } });
});

test('a misconfigured plugin logs the error instead of failing silently', async () => {
  const logs = [];
  const project = tmpdir();
  const hooks = await plugin.server(
    { worktree: project, client: { app: { log: async ({ body }) => logs.push(body) } } },
    { enabled: true, projects: [project] },
  );
  assert.deepEqual(hooks, {});
  assert.equal(logs[0].level, 'error');
  assert.match(logs[0].message, /missing/);
});
