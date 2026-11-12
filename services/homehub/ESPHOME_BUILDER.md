# ESPHome Device Builder

HA ran the ESPHome Device Builder as an add-on: it edits each node's YAML,
compiles firmware and flashes it over the air (OTA). Here it is the `esphome`
service in `docker-compose.yml`: the official prebuilt image
`ghcr.io/esphome/esphome`, pinned to `2026.10.2`, the version the add-on ran.
Nothing is built.

- Config: `./data/esphome` (mounted at `/config`). Toolchains and build caches
  land in `./data/esphome/.esphome` on the first compile of each node (a
  gigabyte or two; that first compile takes several minutes).
- Network: host. It finds nodes over mDNS and talks to them outbound (OTA to
  the node's OTA port, logs and the API on 6053). Nothing has to reach it
  inbound except the admins' browsers.
- Port: `ESPHOME_PORT` (default 6052).

## Who can reach it

The dashboard has no login of its own unless `USERNAME`/`PASSWORD` are set, and
it can flash any node in the house. So:

- **Default: loopback only** (`ESPHOME_ADDRESS` unset = `127.0.0.1`). Reach it
  from a laptop with `ssh -L 6052:127.0.0.1:6052 <user>@<server>` and open
  `http://127.0.0.1:6052/`.
- **From the LAN** (what the Devices page link is for): set all three in `.env`,
  then open the port to the LAN:

  ```
  ESPHOME_ADDRESS=0.0.0.0
  ESPHOME_USERNAME=<an admin name>
  ESPHOME_PASSWORD=<a long random password>
  ```

  ```
  sudo firewall-cmd --permanent --zone=public --add-rich-rule='rule family="ipv4" source address="192.168.2.0/24" port port="6052" protocol="tcp" accept'
  sudo firewall-cmd --reload
  ```

It is deliberately not published through Caddy: OTA only works on the home
network anyway, and the dashboard's own login is a single shared password.

## Linking it from the Devices page

The link is an integration, not a constant: Devices > Integrations > "Set up
ESPHome Builder" (or `POST /hub/flows/esphome_builder` with `{"url": ...}`)
saves its URL (and, when it has one, the login the hub uses to check it is up)
as an `esphome_builder` config entry. The entry is `loaded` while the
dashboard answers. Admins see "ESPHome Builder" on the Devices page only while
that entry exists; removing the entry removes the link. The gateway never
returns the login.

## Copying HA's ESPHome configs

The node YAML, `secrets.yaml` and the builder's own metadata (`.device-builder*`)
are in the HA import copy on the server. Copy them once, before the first start:

```
cd <the stack's directory>
mkdir -p data/esphome
cp -a data/homehub-import/homeassistant/esphome/. data/esphome/
chmod 600 data/esphome/secrets.yaml data/esphome/.device-builder-peer-link-key.bin
```

`secrets.yaml` holds Wi-Fi passwords, API encryption keys and OTA passwords:
keep it out of git and never print it. The nodes keep their keys, so the hub's
esphome entries (which hold the same API keys) are not affected.

Optional, from the full HA disk copy: the add-on's data
(`supervisor/apps/data/5c53de3b_esphome/`) is about 16 GB, almost all of it
build caches. It is not needed; the builder downloads what it needs. If a
compile fails on a missing external component, copy only
`external_components/` from there into `data/esphome/.esphome/`.

## Start

```
docker compose up -d esphome
```

Then add the link (above). The nodes then compile and flash from the builder
as they did from HA.
