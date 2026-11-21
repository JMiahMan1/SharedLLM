// End to end: a synthetic opencode database full of the shared fixture's
// secrets goes through the plugin, the real collector and scrubber, and a local
// stand-in for the gateway. Not one secret, name or key may reach the request
// body or the opencode log. Needs python3 (3.11+) and node >= 22 (node:sqlite).
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { resolveConfig, SyncQueue } from '../lib.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(here, '..', '..', '..');
const PYTHON = process.env.TEACHER_SYNC_PYTHON || 'python3';
const fixture = JSON.parse(fs.readFileSync(path.join(here, 'fixtures', 'secret_cases.json'), 'utf8'));

const join = (pieces) => pieces.map(([text, times]) => String(text).repeat(times)).join('');
const secrets = Object.fromEntries(
  fixture.secrets.map((c) => {
    const value = join(c.pieces);
    return [c.kind, { value, text: (c.context || '{}').replace('{}', value) }];
  }),
);
const fill = (template) => Object.entries(secrets).reduce((s, [k, v]) => s.split(`{${k}}`).join(v.value), template);

const pythonOk = spawnSync(PYTHON, ['-c', 'import sys; assert sys.version_info >= (3, 11)']).status === 0;
let sqliteOk = true;
let DatabaseSync;
try {
  ({ DatabaseSync } = await import('node:sqlite'));
} catch {
  sqliteOk = false;
}

function buildDb(file, sessionID) {
  const db = new DatabaseSync(file);
  db.exec(`CREATE TABLE session_v2 (id text primary key, project_id text, parent_id text, directory text,
    model text, time_created integer, time_updated integer);
    CREATE TABLE session_message (id text primary key, session_id text, type text, seq integer,
    time_created integer, time_updated integer, data text);`);
  const model = { id: 'teacher-ultra', providerID: 'openrouter', variant: 'high' };
  db.prepare('INSERT INTO session_v2 VALUES (?, ?, NULL, ?, ?, ?, ?)').run(
    sessionID, 'p', '/work/SharedLLM', JSON.stringify(model), Date.now(), Date.now());
  const everything = Object.values(secrets).map((s) => s.text).join('\n');
  const tool = (name, input, text, exit) => ({
    type: 'tool', id: `c-${name}`, name, executed: true,
    state: { status: 'completed', input, content: [{ type: 'text', text }], metadata: exit === undefined ? {} : { exit } },
  });
  const rows = [
    ['user', { text: `Fix the pager. ${secrets.household_name.text}; mail ${secrets.email.value}` }],
    ['assistant', { model, content: [
      { type: 'reasoning', text: `Saw ${secrets.github_token.value} earlier` },
      tool('read', { path: '/work/SharedLLM/services/gateway/pager.py' }, `x = 1\n# ${secrets.high_entropy.text}`),
      tool('edit', { path: '/work/SharedLLM/services/gateway/pager.py', oldString: 'n - 1', newString: 'n' }, 'ok'),
      tool('shell', { command: 'pytest -q' }, '3 passed', 0),
      { type: 'text', text: `Done; reach ${secrets.phone.value}. ${secrets.password.text}` },
    ] }],
    ['user', { text: fixture.decrypted_keys_session.goal }],
    ['assistant', { model, content: [
      ...fixture.decrypted_keys_session.steps.map((s) => tool(s.tool, JSON.parse(fill(JSON.stringify(s.input))), fill(s.output), s.exit)),
      { type: 'text', text: fill(fixture.decrypted_keys_session.final_answer) + '\n' + everything },
    ] }],
  ];
  const insert = db.prepare('INSERT INTO session_message VALUES (?, ?, ?, ?, ?, ?, ?)');
  rows.forEach(([type, data], i) => insert.run(`${sessionID}-${i}`, sessionID, type, i + 1, Date.now(), Date.now(), JSON.stringify(data)));
  db.close();
}

test('nothing raw reaches the gateway or the log', { skip: !(pythonOk && sqliteOk) && 'needs python3 >= 3.11 and node:sqlite' }, async () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'trainer-sync-e2e-'));
  const dbFile = path.join(work, 'opencode.db');
  buildDb(dbFile, 'ses_e2e');
  const keyFile = path.join(work, 'key');
  const KEY = 'e2e-key-' + 'Z9'.repeat(10);
  fs.writeFileSync(keyFile, KEY);
  const envFile = path.join(work, '.env');
  fs.writeFileSync(envFile, fixture.env_file_lines.map((l) => l[0] + join(l.slice(1))).join('\n'));

  const bodies = [];
  const auth = [];
  const heartbeats = [];
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      res.setHeader('Content-Type', 'application/json');
      if (req.url === '/api/trainer/teacher/scrub-names') {
        // The household's names come from Identity through the gateway.
        res.end(JSON.stringify({ names: fixture.names }));
        return;
      }
      if (req.url === '/api/trainer/teacher/heartbeat') {
        heartbeats.push(JSON.parse(body));
        res.end('{}');
        return;
      }
      bodies.push(body);
      auth.push(req.headers.authorization);
      const ids = JSON.parse(body).traces.map((t) => t.id);
      res.end(JSON.stringify({ accepted: ids, updated: [], refused: [] }));
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const project = path.join(work, 'project');
  fs.mkdirSync(project);
  const config = {
    ...resolveConfig({
      enabled: true, projects: [project], repo: REPO, python: PYTHON,
      gateway: `http://127.0.0.1:${server.address().port}`, keyFile, envFiles: [envFile],
      stateDir: path.join(work, 'state'), opencodeDb: dbFile, workDir: path.join(work, 'snap'),
    }, project),
    debounceSeconds: 0,
  };
  const logs = [];
  const queue = new SyncQueue(config, { log: async (level, message) => logs.push({ level, message }) });
  const outcome = await queue.enqueue('ses_e2e');
  server.close();

  assert.equal(outcome.ok, true, JSON.stringify(logs));
  assert.equal(bodies.length, 1);
  assert.equal(auth[0], `Bearer ${KEY}`);
  const sent = bodies.join('\n');
  for (const [kind, item] of Object.entries(secrets)) {
    assert.ok(!sent.includes(item.value), `${kind} reached the gateway`);
  }
  for (const name of fixture.names) assert.ok(!sent.toLowerCase().includes(name.toLowerCase()), 'a name reached the gateway');
  const traces = JSON.parse(bodies[0]).traces;
  assert.equal(traces.length, 2);
  assert.equal(heartbeats.at(-1).ok, true, 'the gateway hears how the sync went');
  assert.ok(!JSON.stringify(heartbeats).includes(KEY));
  assert.ok(traces.every((t) => t.scrub.status === 'dropped'), 'a cloud session that touched secrets is dropped whole');
  const logged = JSON.stringify(logs);
  assert.ok(!logged.includes(KEY), 'the key reached the log');
  for (const item of Object.values(secrets)) assert.ok(!logged.includes(item.value));
});

test('the same fixture, without the secret task, keeps the clean task masked', { skip: !(pythonOk && sqliteOk) && 'needs python3 >= 3.11 and node:sqlite' }, () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'trainer-sync-e2e-'));
  const dbFile = path.join(work, 'opencode.db');
  buildDb(dbFile, 'ses_e2e2');
  const db = new DatabaseSync(dbFile);
  db.exec("DELETE FROM session_message WHERE seq > 2");
  db.close();
  const namesFile = path.join(work, 'names.txt');
  fs.writeFileSync(namesFile, fixture.names.join('\n'));
  const out = path.join(work, 'out.jsonl');
  const run = spawnSync(PYTHON, [path.join(REPO, 'scripts', 'collect_teacher_traces.py'), 'collect', '--source', 'opencode',
    '--opencode-db', dbFile, '--names-file', namesFile, '--work-dir', path.join(work, 'snap'), '--out', out,
    '--exclude-file', path.join(work, 'none')], { encoding: 'utf8' });
  assert.equal(run.status, 0, run.stderr);
  const text = fs.readFileSync(out, 'utf8');
  for (const [kind, item] of Object.entries(secrets)) assert.ok(!text.includes(item.value), `${kind} survived`);
  const trace = JSON.parse(text.trim());
  assert.equal(trace.scrub.status, 'masked');
  assert.equal(trace.outcome, 'success');
  assert.match(trace.goal, /Fix the pager/);
});
