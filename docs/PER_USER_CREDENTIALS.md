# Per-User Credentials (and Shared Credentials)

Every person gets their own Home Assistant, Music Assistant, Audiobookshelf and
Nextcloud credentials. Nobody inherits another person's credentials by accident,
and nothing falls back to a global admin account at request time.

Implemented 2026-09-28. The API lives in `services/identity`; the enforcement
points are described at the bottom.

## The rule

1. **A user's own credentials always win.** If you configured a service
   yourself, that is what Jarvis uses for you — always, with no exceptions.
2. **The system default user owns the shared credentials.** That is the account
   named `default` (or whatever it has been renamed to), typically displayed as
   "User 1". Identity identifies it by `is_system_default`, falling back to
   `id == 1` and then `username == "default"` for older rows, so renaming the
   account keeps working.
3. **A grant lets another user borrow the shared credentials, per service.**
   Only an admin can create or revoke a grant, and only for services in
   `SHARED_CREDENTIAL_SERVICES` (`home_assistant`, `music_assistant`,
   `audiobookshelf`, `nextcloud`).
4. **No grant, no credentials.** An unconfigured service is reported as
   unconfigured — the caller gets an error naming the user and the service, not
   the default user's account and not an empty success.
5. **Grants are revocable and recorded.** Each grant stores who granted it,
   when, and an optional note. Emptying the list deletes the grant. Deleting a
   user deletes their grant, so a re-created account starts clean.
6. **Skylight is different on purpose.** It is a household system integration
   (one account for the whole house), so it is not part of the per-service
   grant list. A user's own Skylight credentials still win when they set them.

## What a caller sees

`POST /api/resolve` returns the credentials plus two new fields:

- `credential_sources`: one entry per service — `own` (the user configured it),
  `granted` (borrowed from the system default under a grant), `shared` (the
  service is shared by design, e.g. Skylight) or `absent` (not configured and
  not granted).
- `shared_credential_owner`: the username whose credentials are being borrowed,
  when a grant was actually used.

## API

| Endpoint | Who | What |
|---|---|---|
| `GET /api/users/{username}/credential-shares` | the user themselves, or an admin | the granted services, plus `granted_by` / `granted_at` / `note` / `shared_owner` |
| `PUT /api/users/{username}/credential-shares` | admin only | `{"services": [...], "note": "..."}`; an empty list revokes everything |

Unknown usernames give a 404, an unknown service name gives a 400 that lists the
valid keys, and a non-admin write gives a 403. The gateway proxies both routes
(`GET`/`PUT /api/users/{username}/credential-shares`) so the UI can reach them.

## Where the old behavior was removed

- **Identity** no longer substitutes the system default user's Music Assistant
  credentials for everyone (`use_admin_mass_url` / `use_admin_mass_token` are
  gone); the loop in `/api/resolve` resolves each service through
  `_resolve_service_creds` and records the source.
- **Execution** never borrows. `ServiceNotConfiguredError` is raised by
  `_resolve_mass_ha_creds` and turned into a `status: FAILURE` response that
  names the user, instead of an empty `SUCCESS` with no playlists. The
  announce, entity-search and `/discovery/*` endpoints no longer fall back to
  `resolve_first_user()`; `/discovery/*` take explicit `ha_url` / `ha_token`
  query parameters from the gateway.
- **The gateway** passes the caller's own HA credentials to `/discovery/entities`
  and reports `status: FAILURE` for a user with no Home Assistant configured.
- The execution `UserContext` schema now carries `mass_url` / `mass_token` —
  they used to be dropped by `extra="ignore"`, which is why execution had to
  re-resolve identity for every MA call.

## UI

- **Settings → Identity** — every configured service (Home Assistant, Music
  Assistant, Audiobookshelf, Nextcloud) shows a "Shared Service Access" card:
  the four per-service toggles, who granted them and when, and an optional note.
  Admins can toggle; everyone else sees their current grants read-only. There is
  also a new Music Assistant tile and an Audiobookshelf API-key field.
- **Settings → Admin → Users** — each user row has a "Shared service access"
  button that opens the same editor for that user.

The old "Data Sharing Rule (RAG)" toggle was removed: it posted a
`share_with_all` field that no backend model or schema accepted, so it silently
did nothing. The grants above replace it with something that actually works.

## Testing a service

`POST /api/auth/test-connection` covers Music Assistant and Audiobookshelf API
keys now:

- **Music Assistant** (token only): one JSON-RPC read to `{mass_url}/api` with
  `Authorization: Bearer {mass_token}`. MA 2.10.4 answers 401 for a bad token,
  so this proves both reachability and the token.
- **Audiobookshelf** with an API key: `GET {abs_url}/api/me` with the key as a
  Bearer token (401 for a bad key). With only a username/password it still
  posts to `/api/login`.

Both were verified against the live services on 2026-09-28.

## Still open

- No structured audit log for grants beyond `granted_by` / `granted_at` (see
  the "Current Gaps" section in `services/identity/README.md`).
- `seed.py` re-fills the `default` user's blank credentials from `.env` on every
  startup, so "clear the shared credentials" is undone by a restart. The
  backfill is also keyed on `username == "default"`, so it stops working if that
  account is renamed.
- Music Assistant has no password exchange (no `mass_user` column, and
  `_TOKEN_EXCHANGES` covers Home Assistant, Nextcloud and Audiobookshelf only),
  so MA must be configured by pasting a token.
