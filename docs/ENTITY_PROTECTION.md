# Entity Protection (locking devices to admins and chosen users)

Some things in the house should not be adjustable by everyone. A lock, a
thermostat, the media server, the alarm panel. An admin — or the system default
user — can put an entity under a **lock** and name the handful of users who may
still use it. Everyone else does not merely get a 403 when they try: the entity
is not in their device list at all, so it never appears on their dashboard.

Implemented 2026-09-29. Storage and the admin API live in `services/identity`;
enforcement lives in `services/execution/entity_access.py`.

## The rule

1. **A lock overrides the device assignment.** While an entity is locked, its
   `DeviceAssignment` row is ignored. Being assigned to the thermostat does not
   help if you are not on its permit list.
2. **Admins and the system default user always pass.** They can see and control
   a locked entity whether or not they are on its permit list, so you can never
   lock yourself out of your own house. The default account is the one with
   `is_system_default` set (falling back to `id == 1`, then
   `username == "default"`, so renaming it keeps working).
3. **A permit is per entity, not global.** Naming a user on the hallway
   thermostat does not give them the front door.
4. **A locked entity is invisible to anyone not permitted.** Entity search drops
   it from the results, which means it is absent from the climate dial's room
   tiles, the device-control grid, the entity dropdowns and the voice/agent
   discovery path alike. We never advertise what you cannot use.
5. **Nothing is inherited.** An entity is locked or it is not. There is no
   "protected but inactive" state: releasing a lock deletes the row.
6. **Failures are loud.** An unknown username in a permit list is rejected
   outright rather than stored as a typo'd grant, and a corrupt permit list in
   the database raises a 500 naming the entity — it never degrades to "no
   permits, everyone locked out" or "permits ignored, lock open".

## API

| Endpoint | Who | What |
|---|---|---|
| `GET /api/entity-protection` | admin only | every lock: `entity_id`, `permitted_usernames`, `granted_by`, `granted_at`, `note` |
| `PUT /api/entity-protection/{entity_id}` | admin only | `{"protected": true, "permitted_usernames": ["dana"], "note": "guest room"}`; `protected: false` releases the lock |
| `GET /api/internal/user-device-assignments?username=…` | internal secret | `device_ids`, `protected_entity_ids`, `permitted_entity_ids` (execution only) |

`{entity_id}` is a single path segment matched against
`^[a-z0-9_]+\.[a-z0-9_]+$` — anything else is a 400. A username that does not
exist is a 400 listing the offenders, and **nothing is written**: you cannot
half-apply a permit list. A non-admin write or read is a 403; no header is a
401. The gateway proxies both routes so the UI can reach them.

`protected_entity_ids` in the internal payload is **not** a grant. It exists so
the execution service can tell "assigned but overridden by a lock" apart from
"assigned and effective", and the same payload reports which protected entities
this particular user is permitted on.

## What an outage does

A protection lock must not evaporate because Identity was restarting, and it must
not brick the whole house either. Execution keeps the **last known good**
permission payload per user in a
`LastKnownGoodCache` (`services/common/last_known_good.py`) and degrades
**per entity**:

| Situation | Result |
|---|---|
| Identity answers | normal evaluation |
| Identity unreachable, cache has an answer | the cached answer, plus a `stale=True` flag. A locked entity stays locked; ordinary entities keep working. |
| Identity unreachable, nothing cached | allow, with a loud WARNING |
| The user's row is unknown to Identity | nothing assigned, nothing permitted — fail closed |

The cache has no TTL on purpose. A stale answer is replaced the instant the next
fetch lands, so its age *is* the outage duration; a TTL would only add a second
failure mode (silently expiring back to the "no history" fail-open row). Only
values are cached, never failures.

`/execute/entity/search` deliberately does **not** hide everything during an
outage — `perms is None` means "we do not know who you are", and hiding the whole
house would be a worse failure than showing a light you can no longer control.

The no-cache case is the one real exposure, and it is bounded: it applies only to
a user with no successful permission read in this process's lifetime, it applies
only while Identity is down, and it grants exactly what an unassigned user
would otherwise have to be granted explicitly. Every occurrence logs.

## What is still admin-only regardless of locks

Locks control *which users* may reach an entity. `ha_client.authorize_action`
separately controls *which actions* stay admin-only for everyone, because these
involve physical safety rather than a preference:

- `lock.unlock`, `lock.open`
- `cover.open`
- `alarm_control_panel.alarm_disarm`

`climate.set_temperature` used to be in that list, which made a climate dial
useless to anyone but an admin no matter how they were granted access. It is not
now: a permitted user's setpoint is only as sensitive as their ability to open
the app, and the check is `verify_entity_access` in the handler, so it also
covers the composite night-mode path that calls handlers directly.

## UI

- **Admin → Users & Devices → Entity Protection.** Search an entity, tick the
  users who keep access, optionally add a note, and Protect. Protecting an
  already-locked entity edits that lock in place rather than creating a second
  one. Admins and the default account are listed as "Always allowed, lock or
  not" and are deliberately not offered as checkboxes — a permit for them would
  be a no-op that reads as if it did something.
- **The climate and device-control widgets no longer guess.** They used to
  re-derive "may this user control X" client-side from `GET /api/users/devices`,
  which hid exactly the case that matters: an entity an admin explicitly
  permitted to someone who has no assignment row. They now trust the
  server-filtered listing, and when a configured entity is missing they report
  only how many are unavailable — not the ids the server withheld.
- **`WidgetDef.adminOnly`** is a new, ready-to-use flag for management-oriented
  cards. It is enforced in the single shared `isWidgetVisible` predicate that
  both `widgetStore` visibility paths use, and the store's `isAdmin` defaults to
  `false` so an admin-only card cannot flash for a normal user. **No widget is
  currently marked** — this is the mechanism, available to whoever decides which
  cards should be management-only.

## Where the old behavior was removed

- `DeviceAssignment` no longer decides access on its own. It is one input to
  `EntityPermissions.may_control`, which consults the lock first.
- **Device assignment is now admin-only to change.** `POST /api/devices`,
  `DELETE /api/devices/{id}`, `POST /api/devices/{id}/revoke` and
  `POST /api/users/devices` previously accepted any authenticated user, so
  anyone could self-assign a locked entity and walk straight past the lock.
  Reads are unchanged — a user may still list their own assignments.
- The `authorize_action` climate entry is gone (above), and the climate handler
  now checks entity access itself.

## Still open

- Locks are per entity id, so renaming a device in Home Assistant orphans a
  lock: the old id stays locked and invisible, and the new id is unlocked. There
  is no friendly-name-based re-binding yet.
- Locks are not applied to the entity-touching execution endpoints that never
  called `verify_entity_access` in the first place: `/execute/announce`,
  `/execute/tv_cast` and `/execute/video/play` will act on any entity id a
  caller names. A lock does still hide the entity from search, so this is only
  reachable by asking for the id directly. The group and intercom routes were
  checked and are not a gap — they are group-definition CRUD, not entity
  control.
- A device assignment that names a locked entity is not surfaced anywhere; an
  admin has to notice the lock hides the assignment.
- No structured audit log for lock changes beyond `granted_by` / `granted_at`.
