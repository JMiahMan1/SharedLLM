// Pure helpers for the opencode trainer-sync plugin. No opencode imports here,
// so the tests drive every branch with plain objects and a fake runner.
//
// The plugin never reads a transcript itself and never sees the API key: it
// asks the SharedLLM collector (scripts/collect_teacher_traces.py sync) to
// export, scrub and post one session's new tasks. That keeps a single scrubber
// (services/gateway/trainer/teacher_scrub.py), so the rules cannot drift.

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const SERVICE = 'trainer-sync';
export const PROJECT_FILE = path.join('.opencode', 'trainer-sync.json');
export const EXIT_QUEUED = 3;

const DEFAULTS = {
  enabled: false,
  python: 'python3',
  retryMinutes: 10,
  debounceSeconds: 20,
  stateDir: path.join(os.homedir(), '.local', 'state', 'sharedllm-teacher'),
};

function expand(value) {
  if (typeof value !== 'string') return value;
  return value.startsWith('~/') ? path.join(os.homedir(), value.slice(2)) : value;
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw new Error(`${file} is not valid JSON: ${error.message}`);
  }
}

/**
 * The effective config, or `{ enabled: false, reason }`.
 *
 * Opt-in, twice over: the plugin's own options (opencode.json) must say
 * `enabled: true`, and the project must be listed in `projects` or carry
 * `.opencode/trainer-sync.json` with `enabled: true`. A project file can also
 * opt a project out (`enabled: false`) even when it is listed.
 */
export function resolveConfig(options = {}, worktree = '', env = process.env) {
  const global = { ...DEFAULTS, ...(options || {}) };
  if (!global.enabled) return { enabled: false, reason: 'the plugin options do not set enabled: true' };
  const projectFile = worktree ? readJson(path.join(worktree, PROJECT_FILE)) : null;
  const listed = Array.isArray(global.projects)
    ? global.projects.map((p) => path.resolve(expand(p))).includes(path.resolve(worktree || '.'))
    : false;
  if (projectFile && projectFile.enabled === false) {
    return { enabled: false, reason: `${PROJECT_FILE} opts this project out` };
  }
  if (!listed && !(projectFile && projectFile.enabled === true)) {
    return { enabled: false, reason: `this project is not in projects and has no ${PROJECT_FILE}` };
  }
  const config = { ...global, ...(projectFile || {}), enabled: true };
  for (const key of ['repo', 'keyFile', 'namesFile', 'stateDir', 'workDir']) config[key] = expand(config[key]);
  config.envFiles = (config.envFiles || []).map(expand);
  const missing = [];
  if (!config.repo) missing.push('repo (the SharedLLM checkout)');
  if (!config.gateway) missing.push('gateway');
  if (!config.keyFile && !config.keyEnv) missing.push('keyFile or keyEnv');
  if (config.keyEnv && !env[config.keyEnv]) missing.push(`the variable ${config.keyEnv}`);
  if (missing.length) return { enabled: false, error: true, reason: `trainer-sync is enabled but missing: ${missing.join(', ')}` };
  return config;
}

/** The collector's argv for one session. The key itself is never an argument. */
export function syncArgs(config, sessionID) {
  const script = path.join(config.repo, 'scripts', 'collect_teacher_traces.py');
  const args = [script, 'sync', '--source', 'opencode', '--session', sessionID, '--gateway', config.gateway,
    '--state-dir', config.stateDir];
  if (config.keyFile) args.push('--key-file', config.keyFile);
  else args.push('--key-env', config.keyEnv);
  if (config.namesFile) args.push('--names-file', config.namesFile);
  if (config.noNames === true) args.push('--no-names');
  for (const file of config.envFiles || []) args.push('--env-file', file);
  if (config.keepIps === true) args.push('--keep-ips');
  if (config.opencodeDb) args.push('--opencode-db', expand(config.opencodeDb));
  if (config.workDir) args.push('--work-dir', config.workDir);
  return args;
}

export function flushArgs(config) {
  const script = path.join(config.repo, 'scripts', 'collect_teacher_traces.py');
  const args = [script, 'flush', '--gateway', config.gateway, '--state-dir', config.stateDir];
  if (config.keyFile) args.push('--key-file', config.keyFile);
  else args.push('--key-env', config.keyEnv);
  return args;
}

/** Run a command; resolve with { code, stdout, stderr } (never rejects). */
export function runCommand(command, args, { timeoutMs = 300_000 } = {}) {
  return new Promise((resolve) => {
    let stdout = '';
    let stderr = '';
    let child;
    try {
      child = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    } catch (error) {
      resolve({ code: 127, stdout, stderr: String(error.message || error) });
      return;
    }
    const timer = setTimeout(() => child.kill('SIGTERM'), timeoutMs);
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', (error) => { clearTimeout(timer); resolve({ code: 127, stdout, stderr: String(error.message || error) }); });
    child.on('close', (code) => { clearTimeout(timer); resolve({ code: code ?? 1, stdout, stderr }); });
  });
}

/** The collector prints one JSON line of counts; anything else is not trusted. */
export function parseResult(stdout) {
  const line = String(stdout || '').trim().split('\n').filter(Boolean).pop() || '';
  try {
    return JSON.parse(line);
  } catch {
    return null;
  }
}

export function summarise(result) {
  if (!result) return 'no summary';
  const upload = result.upload || {};
  return [
    `${result.tasks ?? 0} task(s)`,
    `accepted ${upload.accepted ?? 0}`,
    `updated ${upload.updated ?? 0}`,
    `refused ${upload.refused ?? 0}`,
    `queued ${upload.queued ?? 0}`,
    `dropped ${(result.by_scrub_status || {}).dropped ?? 0}`,
  ].join(', ');
}

/**
 * The sync loop: one collector run at a time, debounced per session, with
 * sessions that failed kept (on disk) and retried on a timer. `run` and
 * `log` are injected so tests need neither python nor opencode.
 */
export class SyncQueue {
  constructor(config, { run = runCommand, log, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
    this.config = config;
    this.run = run;
    this.log = log;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.timers = new Map();
    this.chain = Promise.resolve();
    this.pendingFile = path.join(config.stateDir, 'plugin-pending.json');
    this.retryTimer = null;
  }

  pending() {
    return new Set(readJson(this.pendingFile) || []);
  }

  savePending(set) {
    fs.mkdirSync(path.dirname(this.pendingFile), { recursive: true });
    fs.writeFileSync(this.pendingFile, JSON.stringify([...set]));
  }

  /** A session went idle: sync it after the debounce, unless it speaks again. */
  schedule(sessionID) {
    const existing = this.timers.get(sessionID);
    if (existing) this.clearTimer(existing);
    this.timers.set(sessionID, this.setTimer(() => {
      this.timers.delete(sessionID);
      this.enqueue(sessionID);
    }, this.config.debounceSeconds * 1000));
  }

  enqueue(sessionID) {
    this.chain = this.chain.then(() => this.syncOne(sessionID));
    return this.chain;
  }

  async syncOne(sessionID) {
    const { code, stdout, stderr } = await this.run(this.config.python, syncArgs(this.config, sessionID));
    const result = parseResult(stdout);
    const pending = this.pending();
    if (code === 0) {
      pending.delete(sessionID);
      this.savePending(pending);
      await this.log('info', `synced session ${sessionID}: ${summarise(result)}`);
      return { ok: true, result };
    }
    if (code === EXIT_QUEUED) {
      // The collector scrubbed and queued the traces itself; flush will send them.
      pending.delete(sessionID);
      this.savePending(pending);
      await this.log('warn', `session ${sessionID} is queued for retry: ${summarise(result)}; ${(result?.upload?.errors || []).join('; ')}`);
      this.armRetry();
      return { ok: false, queued: true, result };
    }
    // The collector or scrubber failed before anything was scrubbed: remember
    // the session (only its id) and try the whole sync again later.
    pending.add(sessionID);
    this.savePending(pending);
    await this.log('error', `sync failed for session ${sessionID} (exit ${code}): ${String(stderr).trim().slice(0, 500)}`);
    this.armRetry();
    return { ok: false, queued: false };
  }

  armRetry() {
    if (this.retryTimer) return;
    this.retryTimer = this.setTimer(() => {
      this.retryTimer = null;
      this.retry();
    }, this.config.retryMinutes * 60_000);
  }

  async retry() {
    const { code, stdout, stderr } = await this.run(this.config.python, flushArgs(this.config));
    if (code !== 0) {
      const detail = parseResult(stdout)?.errors?.join('; ') || String(stderr).trim().slice(0, 300);
      await this.log(code === EXIT_QUEUED ? 'warn' : 'error', `queued traces still waiting: ${detail}`);
      this.armRetry();
    }
    for (const sessionID of this.pending()) await this.enqueue(sessionID);
  }
}
