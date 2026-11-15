# App vetting

Jarvis checks the apps on a child's or teen's devices, and the apps they ask
for, and tells their parents what is worth knowing: the Play content rating
against the child's age limit, what the household's own model makes of the
listing and its reviews, ads and in-app purchases, and apps not used lately.

Jarvis itself is never registered with Google or published anywhere. The only
contact with Google is reading the public Play listing of other apps.

## Pieces

| Where | What |
|---|---|
| Android `AppInventory` / `AppInventoryRules` | Lists apps a person installed (no system apps, never Jarvis): package, name, installer, first install, last update, launchable, dangerous permission groups, last use and size (both need usage access) |
| `src/lib/appInventorySync.ts`, `AppInventorySync.tsx` | Sends the list from a minor's Android device when it changes, and once a day |
| gateway `app_vetting_routes.py` | `POST /api/apps/inventory` (minors' devices only), the Apps card, the parents' changes, verdicts on app requests |
| telemetry `app_catalog.py` | Keeps each device's list, looks listings up in the background, has the local model review them, notifies parents |
| `services/common/play_listing.py` | Reads a Play page |
| `services/common/app_review.py` | The local model's prompt, and validation of its JSON |
| `services/common/app_vetting.py` | Age policy, verdicts, who hears, unused apps |
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

The page's first 20 reviews are kept for the model (stars, words, date, app
version), never reviewers' names or pictures. An app with no listing (404) is
"not on Google Play" and judged on its permissions and where it came from.

## The local review

The listing and reviews go to the household model (`llm_local_url` with
`guardian_monitor_model`, the same model content monitoring uses). It must
answer JSON with a summary and concerns (contact with strangers, sexual
content, violence, gambling or loot boxes, scams or predatory purchases, heavy
ads, data collection, horror, drugs or alcohol, self-harm, bugs), each with a
severity on content monitoring's scale. Anything else is a failed review with
its reason, retried later with backoff; it is never patched into a verdict. A
quote not found in the reviews is dropped. The review is redone when the
listing changes. Without a configured model the review says which setting is
missing.

## Age policy

A child's birth date (identity `User.birth_date`, set by their parents or an
admin only; not the health profile's self-edited birth year) gives the default
limit: under 10 Everyone, 10 to 12 Everyone 10+, 13 to 16 Teen, 17 Mature 17+.
A parent can set another limit per child, mark an app "always allow" or
"always flag", and choose the days without use before removal is suggested
(default 30).

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

## Removing apps

Block adds the app to `blocked_apps` (it stays installed, and will not open).
Remove adds it to `uninstall_apps`, which only a device with Jarvis as its
device owner acts on; the server refuses Remove (409) when no device of the
child is one, and the card says so and offers Block instead.
