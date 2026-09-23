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

## Version identity (web + native cannot drift)

**`services/ui/package.json` `version` is the single marketing version** for
both the web OTA bundle and the Android `versionName`. `build.gradle` reads it
at build time; `build.js` and `services/ui/Dockerfile` embed it in
`version.json`. Never hardcode a version elsewhere.

**`git_sha` is the single build identity.** Web bundle, APK, and
`/api/app-updates/version` all come from the same short commit. A bugfix
release is one commit: bump `package.json` if the marketing version changes,
push, build UI + APK from that SHA, deploy both artifacts together.

| Channel | Carries | Advances when |
| --- | --- | --- |
| OTA `bundle.zip` | Web/JS fixes | Any `services/ui/src` (or shared web) change |
| APK `versionCode` | Native/widget/plugin fixes | Android/Java/Capacitor native changes — **must** bump `versionCode` |

A web-only fix does **not** require a new APK (users get it via OTA at the same
`git_sha`). A native-only fix **does** require a new APK; bump `versionCode` so
`apk_version_code` in published metadata exceeds older installs.

## Native APK updates

The web bundle cannot change native code. `apk_version_code` in
`data/app_updates/version.json` must be kept in sync with `versionCode` in
`services/ui/android/app/build.gradle`; when the server's value exceeds the
running build, the app offers the APK for manual install.

`versionName` is **not** hardcoded in Gradle — it is parsed from
`services/ui/package.json` so a release cannot ship web `1.4.0` against Android
`1.3.1`.

## Self-signed APK (no Play Store)

Jarvis OS is a personal home app and will **never** ship on Google Play.

| Piece | Location |
| --- | --- |
| Keystore | `services/ui/android/app/release.keystore` (alias `jarvisos`) |
| Passwords | env `KEYSTORE_PASSWORD` / `KEY_PASSWORD`, defaults `jarvis-home-2026` |
| Signing | `build.gradle` — **both** `debug` and `release` use this keystore |
| CI artifact | `jarvis-os-release-apk` → `app-release.apk` (signed) |
| In-app install | `ApkInstall` Capacitor plugin → FileProvider → system installer |

Because debug and release share one certificate, OTA-era installs upgrade in
place without uninstall. First transition from an old debug-keystore build may
require one uninstall/install if signatures differ.

Android still shows the system “Install?” sheet (or requires a one-time
“Install unknown apps” grant for Jarvis OS). Fully silent installs are not
possible on a non-rooted personal device without Play/MDM.

Publish path: CI signs → `deploy_remote.sh` copies APK to
`data/app_updates/app-debug.apk` → Settings → Check for updates → Download APK.

## Verifying a deploy

```bash
# These two SHAs must match.
curl -s https://jarvis.sumemail.com/api/app-updates/version | python3 -m json.tool
curl -s -o .tmp/b.zip https://jarvis.sumemail.com/api/app-updates/bundle.zip
unzip -p .tmp/b.zip version.json
```
