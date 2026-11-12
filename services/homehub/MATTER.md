# Matter: running the Matter Server for homehub

homehub does not speak Matter itself. `drivers/matter.py` (`MatterDriver`) is a
WebSocket client of the Matter Server, the same server Home Assistant used. The
server holds the fabric (root CA, the controller's credentials, each node's data);
we run it on the Jarvis host (192.168.2.205) with a copy of Home Assistant's
storage, and the 14 bulbs keep working without being re-paired.

## Which server, which version

The brief assumed python-matter-server. That is no longer what HA ran:

- HA's add-on was **Matter Server 9.3.0** (`apps.json`: `version: 9.3.0`,
  image `ghcr.io/home-assistant/app-matter-server`).
- Since add-on 9.0.0 the add-on runs the **matter.js-based server**
  (`matterjs-server`), not python-matter-server. Its changelog: "The Python Matter
  Server has been replaced with a matter.js-based implementation ... your data
  migrates automatically". 9.3.0 bundles **matterjs-server 1.5.5**.
- The add-on starts it with `--storage-path /data --fabricid 2 --vendorid 4939`
  (4939 = 0x134B, Nabu Casa) plus the port, log level and primary interface.
- So the image to run is **`ghcr.io/matter-js/matterjs-server:1.5.5`** (the tag
  exists on GHCR, linux/amd64 and linux/arm64). It is a prebuilt image: a pull,
  not a build.
- The WebSocket API is the Python server's (schema 14, `ws://host:5580/ws`,
  same commands and events), which is why HA's integration needed no change and
  why the driver works with either server.

The storage directory therefore holds two generations of data:

| Path in `core_matter_server/` | What it is |
|---|---|
| `server-2-134b/` (`snapshot.json.gz`, `head.json.gz`, `wal/`), `config/`, `certificates/`, `vendors/`, `ota/` | **The live matter.js storage.** Fabric index 2, vendor 0x134B. This is what the server reads. |
| `2580859529081674720.json` (+ `.backup`), `chip.json`, `chip_*.ini`, `credentials/` | The python-matter-server files, last written **2026-06-23** (the migration). Stale; matter.js keeps them as a migration fallback. Copy them along, but do not run python-matter-server on them. |

The compressed fabric id is 0x23D10E1156185BE0 (= 2580859529081674720), which is
the prefix of every Matter unique_id in the entity registry. The driver checks it:
it refuses to start against a server whose `compressed_fabric_id` is different,
because that means the server made a new, empty fabric.

## Where to copy the storage from

**The export copy is empty**: `/home/jeremiah/ha-export/2026-11-11/supervisor/apps/data/core_matter_server/`
on 192.168.2.43 is a root-owned directory with no files in it. Copy from the
read-only mount instead:
`/mnt/haos-data/supervisor/apps/data/core_matter_server/` (sudo on .43). Its
matter.js directories were last written 2026-11-11, i.e. when HA last ran.

## SAFETY: one controller per fabric storage, ever

The copy contains the controller's identity: the same fabric, the same
controller node id, the same operational key, and message counters. Two servers
running from the same storage are the same controller twice. They fight over
CASE sessions and subscriptions, and once one of them has advanced its counters
the other one's messages are rejected as replays until it catches up.

- **HA's Matter Server add-on and this server must never run at the same time.**
  HA is offline now; keep the add-on stopped (and its "Start on boot" off) if the
  HAOS box is ever powered up again.
- After this server has run, **HA's copy is stale for good**. Never start the
  add-on on its old storage again, and never copy the old storage back over this
  one. If HA is ever wanted back, it gets this server's current `/data`, not its
  own.
- Keep one untouched tarball of the copy (below) as the rollback point.

## Host checks on 192.168.2.205 (read-only, done 2026-11-11)

- `enp1s0` (192.168.2.205/24) has an IPv6 link-local address
  (`fe80::f942:4c9b:849c:4bce/64`) and no global IPv6 address; the only IPv6
  route is `fe80::/64`. `disable_ipv6=0`, `forwarding=0`.
- `accept_ra=0` on enp1s0 is NetworkManager's normal setting (it handles router
  advertisements itself; the connection's `ipv6.method` is `auto`).
- Link-local is enough: these are Wi-Fi Matter bulbs on the same /24, and there
  is no Thread border router, so the route-information (RIO) settings for Thread
  do not apply.
- Fedora 43, NetworkManager, avahi-daemon running (mDNS on 5353; matter.js
  shares the port), NTP synchronized, about 5.6 GiB RAM free.
- `firewalld` is active (its rules need sudo to read). `nf_conntrack_udp_timeout_stream`
  is 120 s. The matter.js OS notes ask for at least 1800 s, otherwise a stateful
  firewall can drop device reports that arrive after a quiet spell. If the bulbs
  go unavailable after a few minutes of quiet, raise it:

  ```bash
  sudo sysctl -w net.netfilter.nf_conntrack_udp_timeout_stream=3600
  echo 'net.netfilter.nf_conntrack_udp_timeout_stream = 3600' | sudo tee /etc/sysctl.d/99-matter.conf
  ```

  and allow mDNS on the LAN zone if it is not already (`sudo firewall-cmd --list-all`
  shows it; `sudo firewall-cmd --permanent --add-service=mdns && sudo firewall-cmd --reload`).

## Commands (for the lead; nothing has been run)

### 1. Copy the storage from .43 to .205 (run from the Mac)

```bash
# A rollback tarball first, kept off both servers.
ssh jeremiah@192.168.2.43 'sudo tar -C /mnt/haos-data/supervisor/apps/data -cpzf - core_matter_server' \
  > ~/matter-storage-2026-11-11.tgz
tar -tzf ~/matter-storage-2026-11-11.tgz | grep -c . # sanity: a few hundred entries, server-2-134b/ among them

# Then onto the Jarvis host. The container runs as uid 1000.
ssh jeremiah@192.168.2.205 'sudo mkdir -p /srv/matter-server/data'
cat ~/matter-storage-2026-11-11.tgz | ssh jeremiah@192.168.2.205 \
  'sudo tar -C /srv/matter-server/data --strip-components=1 -xpzf - \
   && sudo chown -R 1000:1000 /srv/matter-server/data \
   && sudo chmod -R u+rwX,go-rwx /srv/matter-server/data \
   && sudo ls -la /srv/matter-server/data'
```

The tarball holds the fabric's private keys: keep it somewhere private and do not
commit it.

### 2. The compose service on .205

```yaml
services:
  matter-server:
    image: ghcr.io/matter-js/matterjs-server:1.5.5
    container_name: matter-server
    restart: unless-stopped
    network_mode: host          # mDNS and link-local IPv6 need the host's interface
    volumes:
      - /srv/matter-server/data:/data
    environment:
      STORAGE_PATH: /data
      PORT: "5580"
      PRIMARY_INTERFACE: enp1s0
      # The add-on's fabric: without these the server would look for the
      # default vendor 0xFFF1 / a random fabric id and start a new, empty fabric.
      VENDOR_ID: "4939"
      FABRIC_ID: "2"
      # The WebSocket API has no authentication: never listen on the LAN address.
      # 127.0.0.1 first (the health check uses the first entry), then the gateway
      # of the docker network homehub runs on (see below).
      LISTEN_ADDRESS: "127.0.0.1,172.18.0.1"
      LOG_LEVEL: info
      ENABLE_TIME_SYNC: "true"   # the add-on's time_sync: auto, and this host is NTP-synced
      TZ: America/Phoenix
```

The same settings as CLI flags, if preferred over environment variables:
`--storage-path /data --port 5580 --primary-interface enp1s0 --vendorid 4939
--fabricid 2 --listen-address 127.0.0.1 --listen-address 172.18.0.1
--enable-time-sync`. `--paa-root-cert-dir` is a python-matter-server flag that
matter.js does not have: the attestation certificates are bundled with the image
and kept current in `/data`; `--certificates-dir` exists only for extra PAAs,
which we do not need.

`172.18.0.1` is a placeholder: use the gateway of homehub's network,
`docker network inspect <sharedllm network> -f '{{(index .IPAM.Config 0).Gateway}}'`.
If homehub itself runs with host networking, `127.0.0.1` alone is enough.

```bash
ssh jeremiah@192.168.2.205
cd <the compose project that gets the service>
docker compose pull matter-server
docker compose up -d matter-server
docker logs -f matter-server   # wait for the nodes to come online; the first start re-interviews
```

Check the fabric before pointing homehub at it: the dashboard
(`ssh -L 5580:127.0.0.1:5580 jeremiah@192.168.2.205`, then http://localhost:5580/)
must show the 14 existing nodes (ids 30, 31, 33, 38, 60-64, 70, 71, 73-75), and the
server info must say compressed fabric id 2580859529081674720. A server that lists
no nodes made a new fabric: stop it and fix the storage path or the vendor/fabric
ids before anything else.

### 3. Point homehub at it

HA stored `ws://core-matter-server:5580/ws` (the Supervisor's internal name). The
driver uses, in order: `HOMEHUB_MATTER_URL`, an `url` option on the config entry,
else the stored URL with `core-matter-server` replaced by `localhost`. In
homehub's compose service:

```yaml
    environment:
      HOMEHUB_MATTER_URL: ws://host.docker.internal:5580/ws
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

## What the export showed about the nodes

From the (June) python storage: 14 nodes, all WiFi, vendors Signify (WiZ A19
Filament: colour temperature only; WiZ BR30) and LIGHTINGINSIDE by Launray (Smart
LED Bulb: colour temperature, hue/saturation and xy). At that time nodes 61, 62,
63 and 71 were unreachable; the matter.js storage is newer and may differ. The
WiZ bulbs are probably also on HA's native `wiz` integration (UDP), so they may
appear twice; that is a registry question, not a Matter one.

## Commissioning new devices later

Through the same WebSocket (the dashboard at port 5580 does both of these):

- New device: `{"message_id": "1", "command": "commission_with_code",
  "args": {"code": "MT:...", "network_only": true}}`. The code is the QR payload
  (`MT:...`) or the 11/21-digit manual code. `network_only: true` for a device
  already on Wi-Fi (or in its pairing mode on the LAN); BLE commissioning of a
  fresh Wi-Fi device needs `set_wifi_credentials` first and a Bluetooth adapter
  (`BLUETOOTH_ADAPTER`/`NOBLE_BINDINGS=dbus` with `/run/dbus` mounted), which this
  compose service does not set up.
- Sharing a device with another controller (multi-admin, e.g. Apple or Google
  Home): `{"command": "open_commissioning_window", "args": {"node_id": 38,
  "timeout": 300}}` answers with `setup_manual_code` and `setup_qr_code` for the
  other app.
- A newly commissioned node gets new entities that are not in the imported
  registry. Its unique_ids follow the same scheme
  (`23D10E1156185BE0-{node:016X}-MatterNodeDevice-{endpoint}-{key}-{cluster}-{attribute}`);
  the driver serves any entity registered with such an id, but creating those
  registry entries is not automatic yet.
