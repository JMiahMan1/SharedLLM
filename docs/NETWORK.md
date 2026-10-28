# Home network monitoring

Jarvis watches the home network through the OpenWrt router and tells admins
when something goes wrong: the internet dropping, a Wi-Fi channel crowded by
one device, a device that keeps falling off Wi-Fi. Admin > Network shows the
same picture live, with each device's data use, a traffic chart, internet
outages and the router's log.

It came out of an outage on 2026-10-15: TVs and smart devices on the 2.4 GHz
"Devices" Wi-Fi kept losing the internet while the Jarvis server, on the same
channel over a slow Wi-Fi link, spent hours downloading images. Nothing said
so at the time, and the router's in-memory log was gone by the next day.

## Pieces

| Where | What |
|---|---|
| `services/control_plane/router_client.py` | Read-only client for the router's ubus JSON-RPC API over HTTPS, with certificate pinning |
| `services/control_plane/router_monitor.py` | Every minute: one snapshot of the router, the internet check, the alert rules, storage in Redis |
| `services/control_plane/network_routes.py` | `/api/network/status`, `/check`, `/log`, `/usage`, `/outages` (internal secret) and the monitor thread |
| `services/gateway/network_routes.py` | The same under `/api/network/*` for admins only, plus `PUT /api/network/settings` |
| `services/identity` | The router login on the system user: `router_url`, `router_user`, `router_pass` (encrypted), `router_cert_sha256`; read back only by `GET /api/internal/router` |
| `services/ui/src/components/admin/NetworkPanel.tsx` | Admin > Network |
| `scripts/router_setup.sh` | Creates the router account and hands its password to Jarvis |

## Setting it up

Run, from a machine that can ssh to the router and the Jarvis server:

```
scripts/router_setup.sh root@<router> 32100 <user>@<server> /home/<user>/SharedLLM
```

It saves a configuration backup to `~/Desktop` first (`sysupgrade -b`), then on
the router:

- writes `/usr/share/rpcd/acl.d/jarvis-monitor.json`, an ACL that can only
  read: `system` info and board, `network.interface` dump, `iwinfo` devices,
  info, client lists and channel survey, `luci-rpc` DHCP leases, the `dhcp`
  UCI config, the system `log`, the two conntrack counters under `/proc`, and
  running `/usr/sbin/nlbw` (nlbwmon's read-only traffic report, nothing else);
- adds an rpcd login `jarvis` with that ACL and a new random password (hashed
  on the router with `uhttpd -m`), and reloads rpcd.

The password goes from the script into Identity through the internal API and
is never printed or saved anywhere else. Running the script again replaces it.
The login can also be entered by hand in Admin > Network > Router login; a
blank password there keeps the stored one.

The first connection pins the SHA-256 of the router's self-signed certificate.
A router presenting any other certificate never receives the password; saving
a different router address clears the pin.

To remove it: delete the ACL file and the `jarvis` login (`uci delete
rpcd.@login[N]; uci commit rpcd; /etc/init.d/rpcd reload`).

### Data use for wired devices

Counting every device's traffic, wired ones included, needs the router's
`nlbwmon` (`apk update && apk add nlbwmon luci-app-nlbwmon`). Its database
stays in `/var/lib/nlbwmon`, which on OpenWrt is RAM, so it does not wear
the flash; Jarvis keeps the history. Without it, data use falls back to the
access points' counters and covers Wi-Fi devices only (Admin > Network says
which).

## What is checked, every minute

- **Internet:** a TCP connection to 1.1.1.1, 9.9.9.9 or 208.67.222.222 on
  port 443 from the control plane. Down after two failed checks in a row.
  This runs even before a router is set up.
- **Router:** reachable or not (alert after three failed checks), restarts
  (its uptime went backwards).
- **Wi-Fi channel load:** from each radio's survey counters, the share of the
  last minute the channel was busy. Each client's share is estimated from the
  bytes it moved over its link rate, which is what makes a slow device that
  downloads nonstop stand out.
- **Reconnects:** `AP-STA-CONNECTED` lines in the router log, per device.
- **Capacity:** DHCP leases in each pool's range, the conntrack table, router
  memory.

## Alerts

Admins get these in the `admin` category, tagged `admin:network:<check>`. Each
is sent once when its condition starts and re-armed only when it has clearly
ended, so a problem that lasts is one notice.

| Alert | When | Re-armed |
|---|---|---|
| The internet is down / is back | 2 failed checks; "back" says for how long | at once |
| Jarvis can't reach the router / can reach it again | 3 failed checks | at once |
| The router restarted | uptime went backwards | every restart |
| \<device\> is crowding the \<band\> Wi-Fi | over 5 minutes, the channel at least 60% busy and the same device using at least 35% of the airtime | the channel under 40% busy for 5 minutes |
| \<band\> Wi-Fi is congested | 10 minutes at least 80% busy with no one device to blame | the channel calm again |
| \<device\> keeps dropping off Wi-Fi | 6 reconnects within 30 minutes | 24 hours later |
| Network nearly out of addresses | a DHCP pool 90% leased | under 80% |
| The router's connection table is nearly full | 85% of conntrack's maximum | under 70% |
| The router is low on memory | under 10% free | above 15% |

"The internet is down" may arrive late: phones away from home reach Jarvis
over the same connection.

## What is stored

In Redis, on the control plane:

- `network:status`: the latest picture (no credentials).
- `network:events`: the last 200 alerts.
- `network:log`: the router's log lines, the newest 25,000 (about a week),
  with the syslog level. The router keeps only 64 KB in memory and loses it on
  restart; this copy is what makes yesterday's problem visible today.
- `network:usage:<hour>`: each device's download and upload in that hour
  (`<mac>:down`, `<mac>:up`), for 35 days. From nlbwmon's per-device counters
  when installed (download is the device's rx), otherwise from the access
  points (download is the AP's tx). A counter that drops is treated as reset.
- `network:outages`: the last 500 internet outages, start and end.

## Admin > Network

- **Data use** for a day, week or month: the household's traffic as a chart
  (hourly up to two days, daily beyond, download and upload stacked), totals
  per local network, and each device's download and upload with its network.
- **Internet outages** in the last 30 days, with how long each lasted, and
  one still going on at the top.

Per-device data use shows what each family member's devices use, so Admin >
Network is for admins only.

## Limits

- Devices behind the extender (192.168.2.230) are one Wi-Fi client of the
  router (its signal and airtime). Whether nlbwmon counts them one by one
  depends on the extender: a bridging one passes their own addresses through,
  one that translates them shows as a single device.
- Airtime shares are estimates from bytes and link rates; they are good at
  naming the device that dominates a channel, not exact.
