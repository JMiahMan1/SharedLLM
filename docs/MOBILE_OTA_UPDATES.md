# Mobile OTA & APK Updates

How the Jarvis OS Android app updates itself, and the invariant that must hold
or the app will restart in a loop.

## Components

| Piece | Location |
| --- | --- |
| Client updater | `services/ui/src/lib/appUpdater.ts` (CapGo `@capgo/capacitor-updater`) |
| Version/bundle API | `services/gateway/main.py` — `/api/app-updates/*` |
| Bundle packaging | `services/ui/build.js` (`packageOtaBundle`) |
| Publishing | `scripts/deploy_local_build.sh`, `scripts/deploy_remote.sh` |
| Published artifacts | `data/app_updates/{version.json,bundle.zip,app-debug.apk}` |

## The core invariant

> The `git_sha` advertised by `/api/app-updates/version` **must** be the
> `git_sha` inside the `version.json` of the `bundle.zip` that endpoint offers.

The client decides "am I up to date?" by comparing the SHA it is running
(`/version.json`, embedded in the bundle) against the advertised SHA. If the two
can never match, the app downloads the bundle, applies it, restarts, still sees
itself as out of date, and repeats — **an endless reload loop**, roughly every
5–10 seconds.

This happened in production: the deploy script wrote `version.json` from the
*server's git HEAD*, while the bundle it published had been built from an
earlier commit. The endpoint advertised `c2946120`; the bundle contained
`2c8dc477`.

### How the invariant is now enforced

1. **Build** — `build.js` refuses to package a bundle without a real git SHA,
   and writes `version.json` into `dist/` before zipping. `services/ui/Dockerfile`
   fails the build if `GIT_SHA` is not passed, and `docker-compose.yml` passes it
   for the `ui` service like every other service.
2. **Publish** — the deploy scripts read the SHA out of the container's own
   `version.json` rather than inventing one from `git rev-parse`, and abort if
   `docker cp` of `bundle.zip` fails (previously `|| true`, which silently left
   stale bundles behind).
3. **Serve** — `/api/app-updates/version` reads `version.json` *out of the
   bundle.zip it will actually serve* and advertises that SHA. If the bundle's
   SHA cannot be read, it reports `bundle_available: false` rather than
   advertising a version it cannot back up.
4. **Client** — `appUpdater.ts` records the SHA it is about to install. On the
   next launch, if it is not running that SHA, the version is blacklisted and
   never retried, so a server-side mistake degrades to "no update" rather than a
   reload loop.

## `set()` vs `next()`

`CapacitorUpdater.set()` **destroys the JS context and reloads immediately** —
it does not stage a bundle for later, and its promise never resolves. Code after
a `set()` call does not run.

Accordingly:

- **Silent/background checks** (3 s after launch, then every 6 h) use `next()`,
  which activates on the next cold start.
- **Only an explicit user-initiated check** (Settings → Check for updates) uses
  `set()`, so the app never restarts under the user without them asking.

## Key names

Metadata uses `git_sha` (snake_case) throughout. The client also accepts
`gitSha` when reading `/version.json`, and the gateway normalizes both, because
an older `Dockerfile` wrote the camelCase spelling.

## Native APK updates

The web bundle cannot change native code. `apk_version_code` in
`data/app_updates/version.json` must be kept in sync with `versionCode` in
`services/ui/android/app/build.gradle`; when the server's value exceeds the
running build, the app offers the APK for manual install.

## Verifying a deploy

```bash
# These two SHAs must match.
curl -s https://jarvis.sumemail.com/api/app-updates/version | python3 -m json.tool
curl -s -o .tmp/b.zip https://jarvis.sumemail.com/api/app-updates/bundle.zip
unzip -p .tmp/b.zip version.json
```
