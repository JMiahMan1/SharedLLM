# Pending Tasks

The one backlog for work that is **not done yet**. Verified against the code
on 2026-10-20. When something here ships, delete it; history lives in git.

## Security

### Other open findings from the 2026-08-22 code review

Re-checked 2026-10-15 and still present. About 80 medium/low findings from
that review were not re-checked one by one; the full list is in git history
(`docs/code_review_2026-08-22.md`, removed in the 2026-10-15 cleanup).

- **Workspace tests**: the Docker reachability test is marked `local_only`
  and skips when no daemon is available; CI does not require the host socket
  (W1).

## Raven capability gaps

- No browser automation and no screenshot feedback, so Raven cannot test UIs.
- No multi-agent delegation; one model instance does all the work.
- CI generation depends on GitHub credentials being present.
- No dependency-install awareness; missing packages are found by failing.
- Loop detection escalates but does not diagnose.
- No shared library/template reuse across workspaces.

## Media player

- Background playback stops when the app is swiped away from recents (the
  WebView is destroyed). Surviving that needs a native audio player.
- The remaining phases of `docs/MEDIA_OVERHAUL.md`.

## Family hub

- Calls: media (audio/video in-app) needs a Talk client library or LiveKit,
  and a reachable STUN/TURN server off the LAN.
