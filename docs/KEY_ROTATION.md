# Key rotation, sign-in reset and the test account

What to do when a key leaks: reset everyone's sign-in, rotate `FERNET_KEY`, and give
automated agents an account of their own so they never need a person's key again.

Nothing in this document, the scripts or the API ever prints a key. The scripts show
fingerprints (the first 12 hex digits of a key's SHA-256) so two places can be compared
without anyone seeing the key.

## What a leak of `.env` plus `identity.db` exposes

`FERNET_KEY` decrypts every `*_enc` column in identity: each person's Jarvis API key
(`api_key_enc`) and the credentials they saved for other services (GitHub, GitLab,
Nextcloud, Home Assistant, Audiobookshelf, mail, Music Assistant, Hugging Face, Skylight,
abuse.ch, the router), linked accounts' OAuth tokens (Spotify) and encrypted household
settings. It also keys the HMAC that identity uses to look keys up.

`INTERNAL_SECRET` sits in the same `.env`. It is not rotated by these tools; see
"Not covered" below.

## 1. Sign everyone out and reset keys

Admin > Security > **Sign everyone out and reset keys** (or `scripts/reset_sign_ins.sh`
on the server). It needs the word `RESET` typed to confirm. Code:
`services/identity/sign_in_reset.py`.

| What | What happens |
|---|---|
| Every person's API key (phones, browsers, the Android app) | Replaced by a new random key. Only a password sign-in hands it out, so every device must sign in again. |
| Extra keys (`APIKey` rows) | Deleted. ESPHome Jarvis satellites (`device:<name>`) and any client someone set up by hand are listed for re-enrolment. |
| Windows PC agent keys (Jarvis Guard) | Cleared; each PC is listed for re-enrolment. |
| Home hub logins | Identity asks the hub (`POST /hub/auth/sign-out-everyone`) to revoke every companion-app login and every person's long-lived token. Service tokens (Music Assistant) are kept and named. Needs `HOMEHUB_URL` in identity's environment; otherwise the report says so and `scripts/reset_sign_ins.sh --hub-only` does it. |
| A device that still has an old key | Gets `401 {"detail": "Your sign-in was reset, please sign in again.", "code": "sign_in_reset"}` from identity and the gateway. The web UI and the Android app clear the stored key (and the native copy the background services read) and show their sign-in screen with that message. |
| A device whose key is unknown for another reason | Gets `401 {"detail": "Invalid API Key", "code": "api_key_invalid"}` from identity's and the gateway's own key checks; the apps sign out saying the session ended. |

The apps sign out **only** on those two codes. Any other 401 (a gateway route passing an
upstream service's 401 through, a request sent without a key) never clears the stored key, so
a family phone is never signed out by someone else's login failing.

The report (also kept as the setting `security_last_sign_in_reset`, no secrets, shown in
Admin > Security after the admin signs back in) lists:

- **Re-enrol by hand**, by kind, with owner and how:
  - `windows_guard`: re-run `agent/jarvis-guard/install.ps1` on that PC as a parent.
  - `esphome_satellite`: pair it again (Devices > Add a device).
  - `api_key`: create a new key in Settings > API keys and give it to the client.
  - `homehub_companion_app`: sign in again in the Home Assistant companion app.
  - `homehub_long_lived_token`: service tokens that were kept; re-issue if exposed.
- **Third-party credentials**: how many accounts hold each kind. These can only be rotated
  at the provider (a new GitHub token, a new Nextcloud app password, and so on); saving the
  new value in Accounts & Integrations replaces the old one.

One person only: the per-user **Reset sign-in** action, or
`POST /api/users/security/sign-in-reset {"confirm": "RESET", "username": "<name>"}`, or
`scripts/reset_sign_ins.sh --user <name>`. The hub is not touched for one person.

API (admin only):

- `GET /api/users/security/sign-in-reset`: preview (counts, re-enrolment list, third-party list, last report)
- `POST /api/users/security/sign-in-reset`: the reset, body `{"confirm": "RESET"}` (+ `"username"`)
- `GET /api/users/security/sign-in-reset/last`: the last report

## 2. Rotate `FERNET_KEY`

`scripts/rotate_fernet_key.sh` on the server. Every service builds its cipher from
`services/common/fernet_keys.py`: it **encrypts** with `FERNET_KEY` and **decrypts** with
`FERNET_KEY`, then `FERNET_KEY_PREVIOUS` (comma separated). Lookup hashes made with the
previous key are still recognised and re-hashed on use. So during the switch both keys open
everything. `.env` is read only at start, so every change is followed by recreating the
services that use the key.

Where values are sealed, and how each is re-encrypted (inside its own container,
`python -m services.common.fernet_rotation --store <store> --mode plan|apply|verify`):

| Service (store) | Data |
|---|---|
| identity (`identity`) | `data/identity/identity.db`: every text column of every table is scanned, so no `*_enc` column or encrypted setting is missed; `user.api_key_hash` is re-computed |
| workspace_runtime (`workspace`) | its volume's `workspace_runtime.db`: workspace env and webhook secrets |
| homehub (`homehub`) | `data/homehub/homehub.db`: sealed config entries (device keys), refresh-token keys, blobs |
| telemetry (`telemetry`) | Redis `tel:concern-image:*` (TTL kept) |

The private chat is not sealed with `FERNET_KEY` (its keys come from the members'
passwords) and is untouched.

Steps (each can be re-run; `rotate` on a half-done rotation resumes it):

1. `scripts/rotate_fernet_key.sh preflight` (the dry run): checks the tools, the `.env`, that
   each service holds the key `.env` names, and that the current key opens every stored value.
   Changes nothing. A value no key opens is listed by table, column and row and stops
   everything until someone has looked at it (`--allow-undecryptable` accepts the list).
2. `scripts/rotate_fernet_key.sh rotate`:
   1. encrypted backup of the three databases (asks for a passphrase twice; gpg AES256, or
      age; the archive is checked by decrypting it before anything changes);
   2. a new `FERNET_KEY` generated into `.env`, the old one moved to `FERNET_KEY_PREVIOUS`
      (`scripts/fernet_env.py`, atomic, mode kept, never printed);
   3. identity, workspace_runtime, telemetry and homehub recreated in that order, each
      checked to hold the new key and the previous one;
   4. every value re-encrypted (one transaction per database), then verified.
3. Reset everyone's sign-in (section 1). Extra keys and PC agent keys exist only as hashes
   keyed with `FERNET_KEY`; the reset re-issues them all, so none is left that only the old
   key could recognise.
4. `scripts/rotate_fernet_key.sh finalize`: verifies that every value opens with the new key
   alone, removes `FERNET_KEY_PREVIOUS`, recreates the four services, verifies again.
   `--recreate-all` also recreates every other service, so no container keeps the old key in
   its environment (they do not use it).

`scripts/rotate_fernet_key.sh status` shows the fingerprints `.env` and each service hold.

Environment: `COMPOSE` (default `docker compose`, e.g. `COMPOSE='sudo docker compose'`),
`ENV_FILE`, `BACKUP_DIR` (default `data/backups`), `WAIT_S`.

### The backup

`data/backups/fernet-rotation-<UTC time>.tar.gpg` (or `.tar.age`), mode 600. The plain
SQLite copies exist only in a mode-700 directory under `.tmp/` while the archive is made and
are removed on every exit. To restore one database:

```bash
gpg -d data/backups/fernet-rotation-<time>.tar.gpg | tar -xf - -C <empty dir>
# then stop the service, copy the .db back into its data directory, start it
```

The backup's values are sealed with the **old** key, which leaked: treat the archive as
sensitive, and delete it once the new key has run for a while.

## 3. The test account for automated agents

`scripts/create_test_account.py` on the machine the agent runs on:

```bash
scripts/create_test_account.py --server https://<jarvis address>
# signs in as an admin (password asked, never stored), then writes the key to
# ~/.config/jarvis/test-agent.key (mode 600, directory 700); the key is never printed
```

Running it again re-keys the account (the old key stops working). The agent sends
`Authorization: Bearer` with the key read from that file.

The account (`User.is_test_account`, `POST /api/users/security/test-account`, admin only):

- no admin, not the system account, family role `other`, no roles, no password (it cannot
  sign in with one), re-asserted every time it is re-keyed;
- identity refuses to make it an admin, a parent or a role holder (422);
- left out of `GET /api/users` (unless `?include_test_accounts=true`, which the admin user
  lists use, marked "Test account"), the family directory, sharing recipients, the family
  page, the household count (`/api/internal/household`, the Garden family size) and family
  notices; the home hub never makes it a user;
- can use its own workspaces and Raven missions like any non-admin account, and the health
  endpoints; it has no access to anyone else's private data, the private chat or Beloved
  (those need a parent role).

## Production runbook

Steps marked **(owner)** need the owner present.

1. **(owner)** Make sure no one is mid-task; a recreate signs nobody out but drops open
   streams for a few seconds.
2. Deploy the services this change touches with the normal deploy (CI builds the images):
   `scripts/deploy_remote.sh <host> <dir> identity gateway workspace_runtime telemetry homehub ui`.
   The compose file changed (identity's `extra_hosts`, `FERNET_KEY_PREVIOUS`), so it must be
   synced; deploy_remote.sh syncs the checkout.
3. On the server: `scripts/rotate_fernet_key.sh preflight`. Read the output; fix anything listed.
4. **(owner)** `scripts/rotate_fernet_key.sh rotate`. Type the backup passphrase (twice).
   Store the passphrase away from the server.
5. **(owner)** Admin > Security > Sign everyone out and reset keys (or
   `scripts/reset_sign_ins.sh --everyone`). Keep the report: it lists what needs re-enrolling.
6. `scripts/rotate_fernet_key.sh finalize --recreate-all`.
7. **(owner)** Sign in again on every phone and browser; re-enrol each listed PC (Jarvis
   Guard installer), ESPHome satellite (pair again) and API-key client; sign in again in the
   Home Assistant companion apps.
8. **(owner)** Rotate every listed third-party credential at its provider and save the new
   value in Accounts & Integrations.
9. On the machine the agent runs on: `scripts/create_test_account.py --server https://<jarvis>`
   **(owner: admin password)**, then point the agent at `~/.config/jarvis/test-agent.key`.

## Not covered

- `INTERNAL_SECRET` was in the same `.env`. Rotating it means a new value in `.env`,
  recreating every service, and updating every outside holder (for example the arcade's
  `JARVIS_INTERNAL_SECRET`). It is not automated here.
- Old backups made before the rotation are sealed with the old key.
