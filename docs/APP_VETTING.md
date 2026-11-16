# App vetting

Jarvis checks the apps on a child's or teen's devices, and the apps they ask
for, and tells their parents what is worth knowing: the Play content rating
and Common Sense Media's age against the child's age limit, what the
household's own model makes of the listing and its reviews (the first page,
the newest and the lowest rated), ads and in-app purchases, a local security
scan (sideloads, risky access, known stalkerware), a malware fingerprint check,
and apps not used lately.

Jarvis itself (`com.jarvisos.app`, `JARVIS_ANDROID_PACKAGE` in telemetry) is
left out of every check: the device never lists it, telemetry never queues it,
and the card drops it.

Jarvis itself is never registered with Google or published anywhere. The only
contact with Google is reading the public Play listing of other apps.

## Pieces

| Where | What |
|---|---|
| Android `AppInventory` / `AppInventoryRules` | Lists apps a person installed (no system apps, never Jarvis): package, name, installer, first install, last update, launchable, dangerous permission groups, last use and size (both need usage access), and for the security scan: risky capabilities, special access switched on, signing certificate SHA-1s, and the base APK's SHA-256 |
| `src/lib/appInventorySync.ts`, `AppInventorySync.tsx` | Sends the list from a minor's Android device when it changes, and once a day |
| gateway `app_vetting_routes.py` | `POST /api/apps/inventory` (minors' devices only), the Apps card, the parents' changes, verdicts on app requests |
| telemetry `app_catalog.py` | Keeps each device's list, looks listings up in the background, has the local model review them, notifies parents |
| `services/common/play_listing.py` | Reads a Play page, and the newest and lowest-rated review pages |
| `services/common/app_review.py` | The local model's prompt, validation of its JSON, the quoted review excerpts |
| `services/common/app_vetting.py` | Age policy (Play and Common Sense), verdicts, who hears, unused apps |
| `services/common/app_security.py` | The local security scan and the stalkerware list |
| `services/common/data/stalkerware_indicators.json` | Snapshot of the Stalkerware Indicators list (CC BY 4.0), refreshed by `scripts/refresh_stalkerware_indicators.py` |
| `services/common/common_sense.py` | Finds and reads a Common Sense Media review page |
| `services/common/malwarebazaar.py` | The MalwareBazaar request and answer |
| identity `/api/apps/{username}/policy*` | Birth date, rating limit, per-app choices, dismissals, block and remove |

## What is sent to Google

One `GET https://play.google.com/store/apps/details?id=<package>&hl=en_US&gl=US`
per package: the package name is the only thing about the family in it. The
request carries a generic browser user agent and `Accept-Language`, no cookies
(the session uses a dummy cookie jar, so none are stored either), no account,
and does not follow redirects. Lookups run in telemetry's background loop, at
most one every `APP_CATALOG_MIN_INTERVAL_S` (15 s); a refusal or failure backs
off from `APP_CATALOG_BACKOFF_BASE_S` (10 min), doubling up to
`APP_CATALOG_BACKOFF_MAX_S` (12 h). A listing is cached for
`APP_CATALOG_TTL_DAYS` (7). No request to Google is ever made while answering
someone. `APP_CATALOG_ENABLED=false` stops lookups; apps then say so and are
judged on their permissions.

The page's first reviews are kept for the model (stars, words, date, app
version), never reviewers' names or pictures. An app with no listing (404) is
"not on Google Play" and judged on its permissions and where it came from.

### More reviews: the newest and the lowest rated

The first page of reviews is Play's "most relevant". The public page loads
more, sorted or filtered by stars, with one POST to
`https://play.google.com/_/PlayStoreUi/data/batchexecute?rpcids=UsvDTd&hl=en_US&gl=US`
whose form names the package, the sort (2 = newest) and an optional star
filter, and nothing else. Telemetry makes three of these per found app per
refresh (newest, one star, two stars; up to 40 reviews each), one per
`APP_CATALOG_MIN_INTERVAL_S`, with the same headers, no cookies and the same
backoff as the listing. The app is reviewed at once with the first page, and
reviewed again once all three pages are in (the entry's hash then covers
them). A page Play answers but this parser cannot read is kept as an error
with its reason (`reviews_read.errors`); the review goes on without it.
Reviewers' names and pictures are never kept.

## Common Sense Media

When `app_vetting_common_sense_enabled` is on (the default), each found app is
looked for on Common Sense Media by its Play title: at most four pages
(`/app-reviews/<slug>` and `/game-reviews/<slug>`, for the whole title and the
title before a subtitle), plus one redirect to another review page. Only the
app's name is in those URLs; a generic browser user agent, no cookies, no
redirects followed automatically. Requests are at least
`COMMON_SENSE_MIN_INTERVAL_S` (20 s) apart, back off from
`COMMON_SENSE_BACKOFF_BASE_S` (15 min) doubling to
`COMMON_SENSE_BACKOFF_MAX_S` (24 h), and an answer is kept for
`COMMON_SENSE_TTL_DAYS` (30). A page counts only when the app it reviews has
the same name.

From the page: the age rating (schema.org `typicalAgeRange`, which must agree
with the "age N+" shown), the whole "Parents need to know" paragraph, and the
parents' and kids' consensus (age, number of reviews, the site's one-paragraph
summary). A page without the review data or without "Parents need to know" is
an error with its reason, never half a review. Critics' and reviewers' names
are never kept. These positions were checked against public pages in
November 2026; the test fixture is a made-up page in the same markup.

States: `found`, `not_found` (no review under the name), `error` (with the
reason), `checking`, `off`.

## Security scan

From what the device already reports (nothing new leaves the house), each app
gets findings, each with a severity and one plain sentence:

- **sideloaded**: the installer is not Google Play (`com.android.vending`):
  unknown installer, another store (Galaxy Store, Amazon Appstore, F-Droid,
  AppGallery) or another app.
- **risky_access**: an accessibility service, device administration, a
  notification listener (each "switched on" when it is active now, high; or
  only declared, medium), reading or sending SMS, the call log, background
  location (high), drawing over other apps, a VPN service, installing other
  apps (medium), and the camera, microphone and location together (medium).
- **stalkerware**: the package name (exact) or a signing certificate SHA-1 is
  on the bundled list (severe).
- **malware**: a MalwareBazaar hit on the app file's SHA-256 (severe).

Findings show on the card. These become flags (and so notices, through the
usual levels): a stalkerware match and a malware hit (severe, category
`security`), and an app from outside Google Play that holds risky access
(`sideloaded_risky`: severe when accessibility, device administration or the
notification listener is switched on, else high). A Play app with risky access
is shown, not flagged: password managers and launchers use these too.

### The stalkerware list

`services/common/data/stalkerware_indicators.json` is a snapshot of `ioc.yaml`
from the open-source [Stalkerware Indicators](https://github.com/AssoEchap/stalkerware-indicators)
project by Echap and contributors, licensed CC BY 4.0
(https://creativecommons.org/licenses/by/4.0/); the attribution travels in
the file and in the card's `stalkerware_list`. Only Android package names and
certificate fingerprints are kept. The snapshot names its commit and date
(the current one: commit `426156175adc84af7fbdd41486c046f9ecb40436`,
2026-10-20). Refresh it on a developer machine and commit the result:
`python scripts/refresh_stalkerware_indicators.py` (or `--commit <sha>`).
The services never fetch it.

## Malware fingerprint check

The device hashes each app's base APK with SHA-256 once per version (cached
by `lastUpdateTime`), skips files over 300 MB (`too_large`), reads at most
1 GB per report (the rest are `deferred` to a later report), and reports
`unreadable` when it cannot read the file. Telemetry looks each new hash up on
abuse.ch MalwareBazaar (`POST https://mb-api.abuse.ch/api/v1/`,
`query=get_info&hash=<sha256>`, with the `Auth-Key` header): only the hash, a
fingerprint of a public file, goes out. At most one lookup per
`MALWAREBAZAAR_MIN_INTERVAL_S` (5 s); a failure backs off from
`MALWAREBAZAAR_BACKOFF_BASE_S` (10 min) doubling to
`MALWAREBAZAAR_BACKOFF_MAX_S` (24 h); "not a known sample" is trusted for
`MALWAREBAZAAR_TTL_DAYS` (7), a hit is kept.

The key is the `malwarebazaar_auth_key` global setting (free at
auth.abuse.ch; Admin > Database & Audit > Global Settings). It is a secret:
identity shows it to the UI only as `***`, saving `***` back keeps it, it is
never logged and never in any card. With no key nothing is asked and every
app's `malware_check` says `not_set_up` ("add an abuse.ch Auth-Key"); a
refused key says so (`error`) until the key changes. A hit is the highest
severity and notifies every parent at severe.

`malware_check.state`: `malware`, `not_known`, `checking`, `not_set_up`,
`error`, `skipped` (too large, unreadable), `no_hash` (the device's Jarvis app
is older than the check).

## Settings

| Setting | Default | Where | What |
|---|---|---|---|
| `app_vetting_common_sense_enabled` | `true` | identity global setting (Admin > Database & Audit > Global Settings) | Common Sense Media lookups on or off |
| `malwarebazaar_auth_key` | empty | identity global setting (same place), secret | abuse.ch Auth-Key for the malware check |
| `COMMON_SENSE_MIN_INTERVAL_S`, `_TTL_DAYS`, `_BACKOFF_BASE_S`, `_BACKOFF_MAX_S` | 20, 30, 900, 86400 | telemetry environment, read at start | Common Sense pacing |
| `MALWAREBAZAAR_MIN_INTERVAL_S`, `_TTL_DAYS`, `_BACKOFF_BASE_S`, `_BACKOFF_MAX_S` | 5, 7, 600, 86400 | telemetry environment, read at start | MalwareBazaar pacing |

Telemetry reads the two global settings from identity (cached a minute) in
its background loop only.

## The local review

The listing and reviews go to the household model (`llm_local_url` with
`guardian_monitor_model`, the same model content monitoring uses). It must
answer JSON with a summary and concerns (contact with strangers, sexual
content, violence, gambling or loot boxes, scams or predatory purchases, heavy
ads, data collection, horror, drugs or alcohol, self-harm, bugs), each with a
severity on content monitoring's scale. Anything else is a failed review with
its reason, retried later with backoff; it is never patched into a verdict. The
model reads up to 20 of Play's first reviews, 15 of the newest and 15 of the
lowest rated, each review once. A quote not found in those reviews is dropped;
the reviews a kept quote came from are kept word for word (stars, date, text,
which page) as the card's `review_excerpts`, at most six. The review is redone when the
listing changes. Without a configured model the review says which setting is
missing.

## Age policy

A child's birth date (identity `User.birth_date`, set by their parents or an
admin only; not the health profile's self-edited birth year) gives the default
limit: under 10 Everyone, 10 to 12 Everyone 10+, 13 to 16 Teen, 17 Mature 17+.
A parent can set another limit per child, mark an app "always allow" or
"always flag", and choose the days without use before removal is suggested
(default 30).

When Common Sense Media has reviewed the app, its age is compared too: with
the child's age, or, when a parent chose a rating limit, with the oldest age
that limit covers (Everyone 9, Everyone 10+ 12, Teen 16, Mature 17+ 17). The
stricter source decides (the one that puts the app at the older age; Play's
youngest ages are Everyone 0, Everyone 10+ 10, Teen 13, Mature 17+ 17, Adults
18; a tie is Play's), and `age_source` says which it was.

## Who sees what, and who hears

Only the child's parents and admins can read or change any of it (identity's
screen-time rule); the child never sees verdicts about their own apps. A flag
over the age limit, a parent's flag, each review concern, and for apps not on
Play their sensitive permissions, carry a level. Each parent hears about newly
flagged installed apps at their own notify level (self-harm always), once per
app and verdict; more than three at once become one notice. "Reviewed, keep"
holds until the verdict changes.

An app request (the guard holding a new install, or asking to unblock one)
shows the verdict on its card and in the parent's notice. When the check is
not done yet the notice says so, and telemetry sends it again with the verdict.

## The Apps card's API

`GET /api/guardian/apps/{username}` (types in `services/ui/src/types/api.ts`,
`GuardianApps` and `AppVerdict`). Each app now also carries:

| Field | What |
|---|---|
| `security` | The scan's findings, most serious first: `{check, key, severity, text, active?, name?, installer?, signature?}` |
| `security_level` | The worst severity among findings and the flags they make, or null |
| `malware_check` | `{state, reason, checked_at?, sha256?, signature?, file_type?, first_seen?, tags?, link?}` |
| `special_access`, `capabilities` | What the device reported (switched on now; declared or asked for) |
| `common_sense` | `{status, reason}` plus, when found, `{url, name, kind, age, age_label, quality_stars, parents_need_to_know, parents_say, kids_say, published}`; null before any lookup |
| `age_source` | `play` or `common_sense`: which rating decided `over_age` |
| `play_over_age`, `common_sense_over_age`, `common_sense_limit` | Each source on its own, and the age Common Sense is held to |
| `review_excerpts` | `[{category, stars, at, text, source}]`, word for word |
| `reviews_read` | `{relevant, newest, lowest, state, errors}`; state is `reading`, `done` or `not_read` |

Flags may now have the reasons `stalkerware`, `malware` and `sideloaded_risky`
(category `security`), and `over_age` carries `source`. The card's `lookups`
gains `common_sense: {enabled, paused_until, paused_reason}` and
`malware: {state, reason, paused_until, paused_reason}`, and the card gains
`stalkerware_list` (source, commit, date, license, attribution). A request card
(`vetting`) also carries `age_source`, `common_sense` and `review_excerpts`.

## Removing apps

Block adds the app to `blocked_apps` (it stays installed, and will not open).
Remove adds it to `uninstall_apps`, which only a device with Jarvis as its
device owner acts on; the server refuses Remove (409) when no device of the
child is one, and the card says so and offers Block instead.
