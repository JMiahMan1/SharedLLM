# Matter: running the Matter Server for homehub

homehub does not speak Matter itself. `drivers/matter.py` (`MatterDriver`) is a
WebSocket client of the Matter Server, the same server Home Assistant used. The
server holds the fabric (root CA, the controller's credentials, each node's data);
we run it on the Jarvis host (192.0.2.205) with a copy of Home Assistant's
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

**The export copy is empty**: `/home/<user>/ha-export/2026-11-11/supervisor/apps/data/core_matter_server/`
on 192.0.2.43 is a root-owned directory with no files in it. Copy from the
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

## Host checks on 192.0.2.205 (read-only, done 2026-11-11)

- `enp1s0` (192.0.2.205/24) has an IPv6 link-local address
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
ssh <user>@192.0.2.43 'sudo tar -C /mnt/haos-data/supervisor/apps/data -cpzf - core_matter_server' \
  > ~/matter-storage-2026-11-11.tgz
tar -tzf ~/matter-storage-2026-11-11.tgz | grep -c . # sanity: a few hundred entries, server-2-134b/ among them

# Then onto the Jarvis host. The container runs as uid 1000.
ssh <user>@192.0.2.205 'sudo mkdir -p /srv/matter-server/data'
cat ~/matter-storage-2026-11-11.tgz | ssh <user>@192.0.2.205 \
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
      TZ: ${TZ:-UTC}   # your zone, e.g. America/Denver
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
ssh <user>@192.0.2.205
cd <the compose project that gets the service>
docker compose pull matter-server
docker compose up -d matter-server
docker logs -f matter-server   # wait for the nodes to come online; the first start re-interviews
```

Check the fabric before pointing homehub at it: the dashboard
(`ssh -L 5580:127.0.0.1:5580 <user>@192.0.2.205`, then http://localhost:5580/)
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
  already on Wi-Fi (or in its pairing mode on the LAN). A fresh Wi-Fi device is
  added over Bluetooth instead: see "Bluetooth: adding new Wi-Fi devices" below.
- Sharing a device with another controller (multi-admin, e.g. Apple or Google
  Home): `{"command": "open_commissioning_window", "args": {"node_id": 38,
  "timeout": 300}}` answers with `setup_manual_code` and `setup_qr_code` for the
  other app.
- A newly commissioned node gets new entities that are not in the imported
  registry. Its unique_ids follow the same scheme
  (`23D10E1156185BE0-{node:016X}-MatterNodeDevice-{endpoint}-{key}-{cluster}-{attribute}`);
  the driver serves any entity registered with such an id, but creating those
  registry entries is not automatic yet.

## Bluetooth: adding new Wi-Fi devices

A new (or freshly reset) Wi-Fi Matter device is not on the network yet. It
advertises over Bluetooth LE, and the controller gives it the Wi-Fi name and
password over that link before it joins. The Matter Server does the Bluetooth
part itself; homehub supplies the code and the Wi-Fi (`matter_ble.py`).

### The adapter

The Jarvis host has two adapters: `hci0`, the built-in one, which stays with
BlueZ for the hub's other Bluetooth work, and `hci1`, a USB dongle given to the
Matter Server. `MATTER_BLUETOOTH_ADAPTER` in `.env` is that adapter's HCI number
(`1` for hci1); the compose service passes it as `BLUETOOTH_ADAPTER`. Blank
means Bluetooth commissioning is off: the server logs `BLE is disabled`, homehub
logs that only devices already on the network can be added, and the Bluetooth
flow refuses with a message naming the setting.

How the server uses it (matterjs-server 1.5.5, `@stoprocent/noble` 2.8):

- **Raw HCI on the adapter**, noble's default on Linux. BlueZ keeps managing
  hci1 (it stays powered and listed in `bluetoothctl list`); noble scans and
  connects next to it. Nothing on the host changes, and nothing has to be redone
  after a reboot. **No sudo is needed.**
- Raw HCI needs `CAP_NET_RAW` in the server process. Docker gives capabilities
  only to uid 0 (a `user: 1000` container with `cap_add` has none in effect), so
  the compose service starts as root with only `NET_RAW`, `SETUID`, `SETGID` and
  `SETPCAP`, and its entrypoint is the image's own `setpriv`, which runs the
  server as uid 1000 (the owner of `./data/matter-server`, as before) with
  `NET_RAW` as its only capability (ambient, bounding set reduced to it). No
  `NET_ADMIN`, not privileged, no device mounts, no SELinux relabel. The same
  entrypoint is used with Bluetooth off.
- Not used: `NOBLE_BINDINGS=dbus` (BlueZ over D-Bus) does not pass the HCI number
  to the D-Bus binding in this version, so it would take the first adapter
  (hci0). `HCI_CHANNEL_USER=1` (exclusive user channel) needs `NET_ADMIN` and the
  adapter powered down in BlueZ before the server starts, which needs sudo on
  every boot; it is the fallback if BlueZ ever gets in the way of a
  commissioning (see below).

Verified on the host on 2026-11-12 with throwaway containers of the same image
(port 5599, storage inside the container, fabric id 99, the real server
untouched): as uid 1000 with `cap_add` the adapter stays `unknown`; as root with
only `NET_RAW`, and through `setpriv` as uid 1000 with only `NET_RAW`, noble
reports `poweredOn` on hci1 (its address, not hci0's) and hears the
advertisements around it; the full server logs `Bluetooth enabled (hci-id=1)`,
`BLE is enabled`, `Bluetooth adapter is powered on, BLE discovery started`, and
`commission_with_code` with a code no device near it has answers
`reason: not-discovered` after its discovery timeout. `set_wifi_credentials` and
`remove_wifi_credentials` under an id of our own work, and the server logs the
credentials as `<redacted>`.

Things to keep in mind:

- **Nothing else should use hci1 through BlueZ** (scans, connections): two
  scanners on one controller refuse each other's commands. BlueZ's default
  controller is hci1 on this host (`bluetoothctl list` marks it `[default]`), so
  anything using BlueZ must select hci0 explicitly.
- **HCI numbers follow probe order.** They have been stable here, but check
  after a reboot or when a USB device is added: `ls -l /sys/class/bluetooth/`
  shows each adapter's USB path, and hci1 must be the dongle
  (`cat /sys/class/bluetooth/hci1/device/uevent` names its USB product).
- **Not yet proven with a real device**: no new Matter device was at hand, so
  the GATT session (PASE and the Wi-Fi handover) has not run with BlueZ also on
  hci1. BlueZ can attach its own GATT client to a connection noble opened. If a
  commissioning fails after the device was found (a BTP or GATT error in the
  server's log), use the exclusive user channel instead: add `NET_ADMIN` to
  `cap_add` and to the three `setpriv` capability lists, set
  `HCI_CHANNEL_USER=1` in the service's environment, and on the host (sudo, each
  boot, before the server starts):

  ```bash
  sudo btmgmt --index 1 power off
  ```

  For every boot, a unit can run it after `bluetooth.service`:

  ```bash
  sudo tee /etc/systemd/system/matter-hci1-release.service >/dev/null <<'UNIT'
  [Unit]
  Description=Give hci1 to the Matter Server (power it off in BlueZ)
  After=bluetooth.service
  Requires=bluetooth.service

  [Service]
  Type=oneshot
  ExecStart=/usr/bin/btmgmt --index 1 power off

  [Install]
  WantedBy=multi-user.target
  UNIT
  sudo systemctl daemon-reload && sudo systemctl enable --now matter-hci1-release.service
  ```

### Turning it on

1. `.env`: `MATTER_BLUETOOTH_ADAPTER=1`.
2. `docker compose up -d matter-server` (recreates the container; the fabric
   storage is untouched). Its log must show `Bluetooth enabled (hci-id=1)` and
   `BLE is enabled`, and the 14 nodes come back as before.
3. In Jarvis, Devices > New devices > Wi-Fi for new devices: set the household
   Wi-Fi (2.4 GHz: Matter Wi-Fi devices do not join 5 GHz networks).

### The household Wi-Fi

A hub setting: the SSID and password, sealed with `FERNET_KEY` in the hub's
store (blob `matter_commissioning_wifi`). No route returns the password; reads
say only whether it is set, and the SSID. Admin routes: `GET
/hub/matter/settings` (`bluetooth.enabled` from the server, `wifi.configured`,
`wifi.ssid`), `PUT /hub/matter/wifi` `{"ssid", "password"}`, `DELETE
/hub/matter/wifi` (the gateway's `/api/homehub/matter/...`). A device can be
given another network (`wifi_ssid` + `wifi_password` in the flow's input).

The Matter Server gets the password only while a device is being added: homehub
stores it under its own credential id (`homehub-commissioning`, never the
server's `default`), passes that id to `commission_with_code`, and removes it
afterwards whatever happened.

### The flow

`POST /hub/matter/commission` with `"method": "ble"` (or the Matter form under
Add by address, `method: ble`):

1. Checks first, so a wrong setup fails at once: the setup code, the Matter
   Server connected, `server_info.bluetooth_enabled` (with Bluetooth off the
   server would silently ignore the Wi-Fi), and a Wi-Fi to give.
2. Then in the background: `set_wifi_credentials`, `commission_with_code`
   (`network_only: false`, `wifi_credentials_id`, `discovery_timeout_s: 90`,
   answer awaited up to 240 s), `remove_wifi_credentials`. One at a time.
3. The answer is `status: pending` with a `session` and `step`
   (`matter_ble_prepare`, `matter_ble_commission` with the time so far,
   `matter_ble_register`); the caller polls with the `session`.
4. The node is registered like every Matter node: device info and entities from
   the node's own clusters (`node_device_info`, `node_entity_specs`).

| Failure | Answer |
|---|---|
| Bluetooth off | 503, names `MATTER_BLUETOOTH_ADAPTER` |
| No household Wi-Fi and none given | 409, names the setting |
| Device not advertising (`reason: not-discovered`) | 404: power, pairing mode, distance |
| Wrong Wi-Fi password or unreachable network | 400: the server's "failed to add/connect to WiFi network" |
| Wrong setup code (`passcode-mismatch`, `invalid-payload`) | 400 |
| Attestation refused (`attestation-rejected`) | 502 with the server's finding |
| Device stopped answering (`timeout`) | 502 |
