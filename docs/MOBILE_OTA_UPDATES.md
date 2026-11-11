# Mobile OTA & APK Updates

How the Jarvis OS Android app updates itself, and the invariant that must hold
or the app will restart in a loop.

## Components

| Piece | Location |
| --- | --- |
| Client updater | `services/ui/src/lib/appUpdater.ts` (CapGo `@capgo/capacitor-updater`) |
| Version/bundle API | `services/gateway/main.py`: `/api/app-updates/*` |
| Bundle packaging | `services/ui/build.js` (`packageOtaBundle`) |
| Publishing | `scripts/deploy_local_build.sh`, `scripts/deploy_remote.sh`, `.github/workflows/android-build.yml` |
| Published artifacts | `data/app_updates/{version.json,bundle.zip,jarvis-os.apk}` |

## The core invariant

> The `git_sha` advertised by `/api/app-updates/version` **must** be the
> `git_sha` inside the `version.json` of the `bundle.zip` that endpoint offers.

The client decides "am I up to date?" by comparing the SHA it is running
(`/version.json`, embedded in the bundle) against the advertised SHA. If the two
can never match, the app downloads the bundle, applies it, restarts, still sees
itself as out of date, and repeats: **an endless reload loop**, roughly every
5-10 seconds.

This happened in production: the deploy script wrote `version.json` from the
*server's git HEAD*, while the bundle it published had been built from an
earlier commit. The endpoint advertised `c2946120`; the bundle contained
`2c8dc477`.

So the gateway always advertises the bundle's own SHA. When the published
`version.json` names a different commit it logs `[AppUpdates] Published
metadata git_sha=… disagrees with bundle git_sha=…`; a full and a short SHA of
the same commit are not counted as a disagreement.

### How the invariant is now enforced

1. **Build.** `build.js` refuses to package a bundle without a real git SHA,
   and writes `version.json` into `dist/` before zipping. `services/ui/Dockerfile`
   fails the build if `GIT_SHA` is not passed, and `docker-compose.yml` passes it
   for the `ui` service like every other service.
2. **Publish.** The deploy scripts read the SHA out of the container's own
   `version.json` rather than inventing one from `git rev-parse`, and abort if
   `docker cp` of `bundle.zip` or `version.json` fails (previously `|| true`,
   which silently left stale bundles behind).
3. **Serve.** `/api/app-updates/version` reads `version.json` *out of the
   bundle.zip it will actually serve* and advertises that SHA. If the bundle's
   SHA cannot be read, it reports `bundle_available: false` rather than
   advertising a version it cannot back up.
4. **Client.** `appUpdater.ts` records the SHA it is about to install. On the
   next launch, if it is not running that SHA, the version is blacklisted and
   never retried, so a server-side mistake degrades to "no update" rather than a
   reload loop.

## `set()` vs `next()`

`CapacitorUpdater.set()` **destroys the JS context and reloads immediately**.
It does not stage a bundle for later, and its promise never resolves. Code after
a `set()` call does not run.

Accordingly:

- **Silent/background checks** (3 s after launch, then every 6 h) use `next()`,
  which activates on the next cold start.
- **Only an explicit user-initiated check** (Settings → Live Over-The-Air
  Updates → **Check Now**) uses `set()`, so the app never restarts under the
  user without them asking.

## Key names

Metadata uses `git_sha` (snake_case) throughout. The client also accepts
`gitSha` when reading `/version.json`, and the gateway normalizes both, because
an older `Dockerfile` wrote the camelCase spelling.

## Version identity (web + native cannot drift)

**`services/ui/package.json` `version` is the single marketing version** for
both the web OTA bundle and the Android `versionName`. `build.gradle` reads it
at build time; `build.js` and `services/ui/Dockerfile` embed it in
`version.json`. Never hardcode a version elsewhere. (`build.gradle` keeps a
literal only as the fallback for a checkout without `package.json`.)

**`git_sha` is the single build identity.** Web bundle, APK, and
`/api/app-updates/version` all come from the same commit. A bugfix release is
one commit: bump `package.json` if the marketing version changes, push, and let
CI build the UI image and the APK from that SHA.

| Channel | Carries | Advances when |
| --- | --- | --- |
| OTA `bundle.zip` | Web/JS fixes | Any `services/ui/src` (or shared web) change |
| APK `versionCode` | Native/widget/plugin fixes | Every CI APK build: `build.gradle` uses `GITHUB_RUN_NUMBER` (minimum 30) |

A web-only fix does **not** require a new APK (users get it via OTA at the same
`git_sha`). A native-only fix **does** require a new APK. `versionCode` needs no
manual bump: each run of the APK workflow has a higher run number, so the
published build exceeds older installs. A local Gradle build without
`GITHUB_RUN_NUMBER` gets versionCode 30.

## Native APK updates

The web bundle cannot change native code. When the published APK is newer than
the running build, the Android app updates itself (below), and Settings still
offers it for manual install.

### Automatic install

At launch and every six hours `autoUpdateApk` (`lib/appUpdater.ts`) downloads a
newer published APK, verified against `apk_sha256`. It installs the APK the
next time the app goes to the background (`pause`), never during a live call.
Each version is tried at most once every six hours. Nothing happens until
"Install unknown apps" is allowed for Jarvis OS; until then Settings shows
**Allow updates**.

The install is a `PackageInstaller` session (`SelfUpdate.java`), not the "open
this .apk" hand-off. On Android 12+ a session that updates the installing app
asks for nothing once Jarvis is its own installer of record
(`USER_ACTION_NOT_REQUIRED`). The first time, and on older Android, Android
still shows its install screen. If the app is in the background, a "Jarvis OS
update ready" notification opens that screen instead. A device-owner phone
installs silently (`DeviceOwnerGuard.installApkSilently`).

**Jarvis Guard.** Android's update screen names "Jarvis OS", just like the
uninstall dialog, so the guard used to cover it on a child's phone, and every
update needed a parent's ten-minute unlock. While an update that Jarvis itself
started is in progress (`SelfUpdate.inProgress`: 5 minutes from the commit,
reopened when the person taps the notification), the guard lets the package
installer through (`GuardRules.isOwnUpdateScreen`). The uninstall dialog and
Settings pages about Jarvis stay covered, even during that window. Android
refuses an update that is not signed with Jarvis's own key, so the window
cannot be used to put anything else over Jarvis.

**The server never takes the advertised version on trust.** `apk_version_code`
is read out of `AndroidManifest.xml` inside the APK it is actually serving
(`services/gateway/apk_manifest.py`, pure stdlib, no `aapt` needed). This is
deliberate: an earlier version parsed `versionCode` out of
`services/ui/android/app/build.gradle` on every *bundle* deploy, so the
advertised number drifted ahead of the published artifact (server said 23, the
APK on disk was 22) and a user who installed the newest APK on offer was told
to update forever. One source of truth, the artifact, removes that class of
bug.

Consequences:

- The publish endpoint **rejects** an APK whose `versionCode` it cannot read
  (HTTP 400), rather than advertising a number it cannot honour. A claimed
  `apk_version_code` form field that disagrees with the file is logged and
  ignored.
- An APK present but unreadable is **withheld** (`apk_available: false`); a
  misdescribed APK is worse than none.
- A bundle-only deploy never changes the advertised APK version, so deploying
  web code can never invent a pending native update.
- The deploy scripts deliberately no longer write `apk_version_code`.

`versionName` is **not** hardcoded in Gradle: it is parsed from
`services/ui/package.json` so a release cannot ship web `1.4.0` against Android
`1.3.1`.

The APK is served at `/api/app-updates/jarvis-os.apk`; the old
`/api/app-updates/app-debug.apk` name redirects there (307).

### Publishing

`.github/workflows/android-build.yml` runs on pushes and pull requests to
`main` and `microservices` that touch `services/ui/**` (and on manual
dispatch). It builds a debug and a release APK, uploads them and the OTA web
bundle as workflow artifacts, and on pushes (never on pull requests) publishes
the release APK to `POST /api/app-updates/publish` with the `X-Internal-Secret`
header. It does not send a version code; the gateway derives it. Publishing
needs the `APK_PUBLISH_URL` and `INTERNAL_SECRET` repository secrets; without
them the step skips with a notice and the APK remains available as the
`jarvis-os-release-apk` workflow artifact. An HTTP 413 from the proxy in front
of the server fails the step with a hint to raise nginx's
`client_max_body_size`.

## Self-signed APK (no Play Store)

Jarvis OS is a personal home app and will **never** ship on Google Play.

| Piece | Location |
| --- | --- |
| Keystore | **Not in git.** Local: `services/ui/android/app/release.keystore` (alias `jarvisos`); CI: secret `RELEASE_KEYSTORE` (base64) |
| Passwords | env `KEYSTORE_PASSWORD` / `KEY_PASSWORD` (GitHub Actions secrets). Local fallback: gitignored `keystore.properties`. **No default password in source.** |
| Signing | `build.gradle`: **both** `debug` and `release` build types use this keystore |
| CI artifact | `jarvis-os-release-apk` → `app-release.apk` (signed) |
| In-app install | `ApkInstall` Capacitor plugin (`ApkInstallPlugin.java`) → `SelfUpdate` PackageInstaller session (FileProvider hand-off as fallback) |

The keystore file and passwords must never appear in the repository. CI restores
the keystore from secrets (and fails if any of the three secrets is missing)
before `assembleDebug` / `assembleRelease`.

Because debug and release share one certificate, OTA-era installs upgrade in
place without uninstall. First transition from an old debug-keystore build may
require one uninstall/install if signatures differ.

Jarvis OS needs a one-time "Install unknown apps" grant. After the first
in-app session install, Android 12+ updates it without a tap (see Automatic
install). Older Android always shows the system "Install?" sheet.

Publish path: CI signs and publishes the APK to the gateway (above);
`deploy_remote.sh` can also download the `jarvis-os-release-apk` artifact and
copy it to `data/app_updates/jarvis-os.apk`. On the phone: Settings → Live
Over-The-Air Updates → **Check Now**, then Download and Install.

## Verifying a deploy

```bash
# These two SHAs must match.
curl -s https://jarvis.sumemail.com/api/app-updates/version | python3 -m json.tool
curl -s -o .tmp/b.zip https://jarvis.sumemail.com/api/app-updates/bundle.zip
unzip -p .tmp/b.zip version.json
```
