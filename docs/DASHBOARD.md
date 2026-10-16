# Dashboard, App Shell, Settings and Sign-in

How the everyday screens work and what backs them. API details are in
`docs/api_reference.md`.

## App shell (`services/ui/src/components/layout`)

- **Navigation** comes from one list, `layout/navItems.ts`, used by the desktop
  sidebar, the phone bottom bar and the header's page finder. Phones pin
  Home, Wander, Family, Media and Health; every other page the user may open
  (Bible, Notes, Calendar, Workspaces, Knowledge, Docs, Settings, and for
  admins Lab and Admin) is under **More**. Lab and Admin are admin-only.
- **Phone vs desktop**: the phone shell (bottom bar, compact header) is used in
  the Android app and in browsers narrower than 768px.
- **Header**: page finder (desktop), location-sharing dot, **Pulse** (gateway
  readiness), the notification bell and the account menu (Profile Settings,
  Accounts & Integrations, Admin, Log Out).
- **Notification bell**: the signed-in user's own notices from the telemetry
  outbox (`GET /api/telemetry/notifications`): family arrivals and
  departures, leave-by reminders, report results. These are the same notices
  the Android app raises as system notifications through its long poll
  (`ChatNotifyService`), and that browsers get by web push when it is turned
  on in Settings. The green dot means notices newer than the last time the
  bell was opened (remembered on the device).
- **Sidebar footer**: the real readiness ("All services online" or "N services
  degraded") and the gateway build.
- **Server banner** (Android app only): shown while the configured server is
  unreachable or unset, with a field to change it. Checks `GET /health` every
  30 s; hidden when connected.
- Readiness (`/health/ready`) is one shared query (`hooks/useSystemHealth.ts`),
  polled every 15 s.

## Dashboard (`pages/Dashboard.tsx`)

- **Search box**: semantic search over the user's RAG collections
  (`GET /api/search`) as you type; shows the best passage and its sources.
- **Voice** button opens the voice assistant; what it hears goes into search.
- **Widget grid**: widgets, their order, size and visibility are per account in
  identity (`/api/widgets/settings`). The UI's widget list and identity's
  `WIDGET_DEFAULT_SIZES` must match (a test enforces it). The gear opens
  Dashboard Settings: show/hide, size, order, Reset (size, position and
  visibility only; favourite devices and widget config are kept) and, in a
  browser only, Export/Import of the layout. Showing Quick Assistant is the
  same as enabling it.
- **Live Service Status** (desktop for everyone, phones for admins): one card
  per service from `/health/ready`; tap for revision and last restart.
- **Recent Problems** (admins): failed requests from the central log
  (`GET /api/admin/logs?level=warning,error`; 4xx are warnings, 5xx errors).
  **Clear** asks first and then empties the whole system log.
- **Workspaces** (desktop, phones for admins) and **Raven** missions (admins).

Widget notes: Device Control groups by Home Assistant area and has one owner
per device (admins always have control); Energy shows about the last 100
minutes in 5-minute buckets of household totals, from the execution
telemetry loop (every 60 s); timers pause and resume by id and paused timers
stay listed. A timer that runs out on screen plays a short two-tone chime
(`lib/chime.ts`, synthesised with Web Audio, no sound file). Browsers only
allow sound after a tap, so audio is unlocked by the first tap on the page and
again when a timer is set. A speaker picked in the widget still gets the
spoken alert, and the phone still gets its notice.

## Settings (`pages/Settings.tsx`)

- **Notifications**: in a browser, a switch for web push; in the Android app,
  a note that notices arrive as Android notifications (use Android's
  per-channel settings to silence them). Everything also appears under the bell.
- **Reports**: one schedule per report type and frequency; saving again
  replaces it and Pause turns it off. Reports use the Reports model, falling
  back to the Librarian and then the Coding model.
- **Admin PIN**: Android app only; changing or removing it needs the current PIN.
- System configuration is edited only in Admin → Integrations.
- **Accounts & Integrations** opens `/identity`.

## Accounts & Integrations (`pages/Identity.tsx`)

Profile (name, picture), service credentials, voice signature, devices and
API keys. See `docs/INTEGRATIONS.md` for each service's fields and
`docs/PER_USER_CREDENTIALS.md` for shared credentials.

## Sign-in (`pages/Login.tsx`)

Username and password against identity (`POST /api/auth/login`); the last
username is remembered. In the Android app the form also has the server URL
(default `https://jarvis.sumemail.com`; a bare host is taken as https) with a
connection test. There is no fingerprint sign-in: no password is stored on the
phone, so the old fingerprint button could never work.

## Docs (`pages/Docs.tsx`)

Lists README.md and every `docs/*.md` from the server (`GET /api/docs`) and
renders the chosen one; on phones the list is a picker. The (?) help buttons
on other pages open a section of one of these documents.
