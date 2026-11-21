# teacher-sync-launchd

The Mac side of the hands-off teacher pipeline (`docs/TEACHER_TRACES.md`, section 8):
a launchd agent that, every 30 minutes and at login, syncs every opencode and
Cline session touched since the last run to the Jarvis Trainer, flushes the
upload queue, and reports a heartbeat.

```bash
tools/teacher-sync-launchd/install.sh --gateway https://<jarvis> --key-file ~/.config/sharedllm/trainer-key
tools/teacher-sync-launchd/uninstall.sh
```

- `install.sh` writes `~/Library/LaunchAgents/com.sharedllm.teacher-sync.plist`
  and `~/.config/sharedllm/teacher-sync.env` (mode 600), then reloads the agent.
  Neither file holds a secret: they name the key file, which must be mode 600.
  Running it again changes only what differs and says so.
- `run.sh <config>` is what launchd runs: `collect_teacher_traces.py sync --source all`,
  then `flush`, appended to `~/Library/Logs/sharedllm-teacher/sync.log`
  (rotated at 1 MiB, 5 kept). Exit 3 from the collector (work queued for a
  retry) is not a launchd failure.
- Options: `--python <path>`, `--interval <seconds>` (default 1800),
  `--names-file <path>` (extra names; Identity supplies the household's).

Tests: `services/gateway/tests/test_teacher_sync_launchd.py` (stubbed launchctl and python).
