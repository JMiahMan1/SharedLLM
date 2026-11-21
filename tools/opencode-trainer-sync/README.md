# opencode-trainer-sync

An opencode plugin that sends this project's finished opencode tasks to the
Jarvis Trainer as scrubbed teacher traces (see `docs/TEACHER_TRACES.md`).

When a session goes idle (debounced, 20 s by default) it runs the SharedLLM
collector for that one session:

```
python3 <repo>/scripts/collect_teacher_traces.py sync --source opencode --session <id> ...
```

The collector clones the opencode database, reads it read-only, keeps only the
tasks not sent before, scrubs them with the one scrubber
(`services/gateway/trainer/teacher_scrub.py`) and posts them to
`POST /api/trainer/teacher/traces`. The plugin itself never reads a transcript
and never sees the API key: it passes the key file's *path*.

- **Nothing raw leaves the Mac.** A cloud session that touched secrets is sent
  as content-free stubs only.
- **Nothing is dropped silently.** A failed upload is queued (scrubbed, on disk)
  and retried every `retryMinutes`; a collector or scrubber failure keeps the
  session id and retries the whole sync. Both are written to opencode's log
  under `service: trainer-sync` (`opencode` log file, or the TUI log view).
- **Opt-in twice.** Off unless the plugin options say `enabled: true` *and* the
  project is listed in `projects` or has `.opencode/trainer-sync.json` with
  `{"enabled": true}`. That file with `{"enabled": false}` opts a project out.

## Install (after review)

1. Prerequisites: `python3` 3.11+ and a SharedLLM checkout on the Mac; the names
   file and key file from the runbook in `docs/TEACHER_TRACES.md`.
2. Copy or link the plugin where opencode loads local plugins:
   ```bash
   mkdir -p ~/.config/opencode/plugins/trainer-sync
   cp <repo>/tools/opencode-trainer-sync/{index.js,lib.js,package.json} ~/.config/opencode/plugins/trainer-sync/
   ```
3. Register it with options in `~/.config/opencode/opencode.jsonc`:
   ```jsonc
   "plugin": [
     "@tarquinen/opencode-dcp@latest",
     ["file:///Users/<you>/.config/opencode/plugins/trainer-sync/index.js", {
       "enabled": true,
       "repo": "~/Code/SharedLLM",
       "gateway": "https://<jarvis>",
       "keyFile": "~/.config/sharedllm/trainer-key",
       "namesFile": "~/.config/sharedllm/teacher-names.txt",
       "projects": ["~/Code/SharedLLM", "~/Code/alpaca"]
     }]
   ]
   ```
   Optional: `keyEnv` (a variable name instead of `keyFile`), `envFiles` (more
   `.env` files whose values are masked), `keepIps`, `python`, `stateDir`
   (default `~/.local/state/sharedllm-teacher`), `debounceSeconds`,
   `retryMinutes`, `opencodeDb`, `workDir`.
4. Restart opencode. The log shows `trainer-sync ... enabled for <project>`, or
   the reason it is disabled (an error naming what is missing).

## Tests

```bash
cd tools/opencode-trainer-sync && npm test     # node --test; the e2e test needs python3 >= 3.11 and node >= 22
```

The end-to-end test builds an opencode database full of the shared fixture's
synthetic secrets (`test/fixtures/secret_cases.json`, also read by the Python
tests), runs the plugin, the real collector and a local stand-in gateway, and
checks that no secret, name or key reaches the request or the log.
