# Accounts & Integrations

The **Accounts & Integrations** page (`/identity`, also reached from the
account menu and from Settings) is where each person connects the services
Jarvis signs in to on their behalf, records their voice, links devices and
makes API keys. The help (?) buttons on that page open the matching section
below.

Credentials are per person and stored encrypted (Fernet) by the identity
service. Secrets are never sent back to the browser: a saved password or token
shows as **Saved**, and leaving the field blank on save keeps it. **Test** uses
the saved secret when the field is blank. How shared (household) credentials
are borrowed is described in `docs/PER_USER_CREDENTIALS.md`.

## Home Assistant

- **Instance URL**: the address Jarvis can reach, e.g. `http://homeassistant.local:8123`.
- **Long-Lived Token**: Home Assistant → your profile → Security → Long-lived access tokens.

Test calls `GET /api/config` with the token.

## Nextcloud

- **Cloud Base URL**: your Nextcloud address.
- **Cloud Username**: your Nextcloud login (pre-filled from your Jarvis name).
- **App Password**: Nextcloud → Personal settings → Security → Create new app password.

Used for files, Talk (family chat), notes and calendars. Test performs an OCS API request.

## Audiobookshelf

- **Server URL**
- **API Key** *or* **Username** and **Password**. An API key alone is enough
  (Audiobookshelf → Settings → Users → your user → API token).

Test calls `GET /api/me` with an API key, or `POST /api/login` with a
username and password.

## Music Assistant

- **Server URL**: e.g. `http://music-assistant.local:8095`.
- **Access Token**: a long-lived token from Music Assistant (Settings → Users / tokens).

Test makes one JSON-RPC read to `<server>/api` with the token.

## Private Git

- **Git Endpoint**, **Auth Username**, **Auth Password** (or token) for a
  self-hosted git server Jarvis may read and push branches to. There is no
  connection test for this one; it is checked when used.

## GitHub

- **GitHub Username** and a **Personal Access Token** (repo scope).
- **Protected Branches**: branch names Jarvis must never push to directly
  (type a name and press Enter or comma).

Test calls GitHub's `GET /user` and reports the login.

## GitLab

- **GitLab URL** (blank for gitlab.com), **GitLab Username**, **Access Token**.

Test calls GitLab's `GET /api/v4/user`.

## Skylight

One household account, set up by an admin on the system default user. Everyone
else can only turn Skylight chores and rewards on or off for themselves; the
fields are read-only for them.

## OpenRouter

- **Cloud Base URL** and **Cloud API Key** for cloud models through
  OpenRouter. Not shown on the system default user. No connection test.

## External Client Setup

API keys let OpenAI-compatible clients (OpenWebUI, LobeChat, scripts) talk to
Jarvis as you. **Generate New Key** asks which client the key is for and then
shows the key **once** with a copy button; only its prefix is kept on the
server. Point the client at `https://<your server>/v1` with the key as the
bearer token. Revoke a key with the bin icon (it asks to confirm).
