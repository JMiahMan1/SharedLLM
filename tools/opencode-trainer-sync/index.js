// opencode plugin: send this project's finished opencode tasks to the Jarvis
// Trainer as scrubbed teacher traces.
//
// When a session goes idle, the plugin (after a short debounce) runs the
// SharedLLM collector for that one session:
//
//   python3 <repo>/scripts/collect_teacher_traces.py sync --session <id> ...
//
// The collector reads the opencode database read-only (from a clone), keeps
// only the tasks not sent before, scrubs them with the same scrubber every other
// path uses, and posts them; when the gateway cannot be reached it queues the
// scrubbed batch on disk and this plugin retries it. Nothing raw leaves the
// machine, and the API key stays in its file (the plugin only knows the path).
//
// Opt-in: see README.md. Disabled unless the plugin options say enabled: true
// and the project is listed or carries .opencode/trainer-sync.json.

import { resolveConfig, SERVICE, SyncQueue } from './lib.js';

// Only the default export: opencode's loader treats exported functions as
// plugins too, and a second registration would sync every session twice.
const TrainerSyncPlugin = async (ctx, options) => {
  const log = async (level, message) => {
    try {
      await ctx.client.app.log({ body: { service: SERVICE, level, message } });
    } catch {
      // The log endpoint itself failed: stderr is the last place left to say it.
      console.error(`[${SERVICE}] ${level}: ${message}`);
    }
  };

  let config;
  try {
    config = resolveConfig(options, ctx.worktree || ctx.directory);
  } catch (error) {
    await log('error', `disabled: ${error.message}`);
    return {};
  }
  if (!config.enabled) {
    if (config.error) await log('error', config.reason);
    return {};
  }

  const queue = new SyncQueue(config, { log });
  await log('info', `enabled for ${ctx.worktree || ctx.directory}; posting to ${config.gateway}`);
  if (queue.pending().size) queue.armRetry();

  return {
    event: async ({ event }) => {
      if (event?.type === 'session.idle' && event.properties?.sessionID) {
        queue.schedule(event.properties.sessionID);
      }
    },
  };
};

export default { id: 'opencode-trainer-sync', server: TrainerSyncPlugin };
