# DNS Service

`services/dns/main.py` runs as container `sharedllm_dns`, the one name server
every SharedLLM container uses. It replaces the earlier dnsmasq / `dns-sync` /
`dns-forwarder` / DNS-relay designs, none of which run any more (the old
`/api/webhooks/dns-sync` gateway route is gone too).

## Where it listens

| What | Address |
|---|---|
| DNS (UDP 53, `DNS_PORT=53` in compose) | `172.26.0.254` on the `sharedllm_default` bridge |
| DNS from the host / LAN | host port `15353` (compose maps UDP and TCP, but the service only answers UDP) |
| HTTP: `/health`, `/registry/status`, `/registry/register` | port `8009` inside the container |

Bridge containers reach it through Docker's embedded resolver. The
host-networked **execution** service has no embedded resolver, so it mounts
`services/execution/resolv.conf`: `nameserver 172.26.0.254` first (it knows
the `.local` hosts that plain LAN DNS does not), the router as fallback,
`options timeout:2 attempts:2`.

`/registry/status` and `/registry/register` require `X-Internal-Secret`:
registry answers are consulted before upstream DNS, so an open write would let
any container spoof names for the stack.

## How a name is answered

In order:

1. **Static mappings**: the `dns_mappings` global setting in Identity
   (edited in the UI's DNS panel through the gateway's `/api/dns` routes, or
   `/api/admin/dns`), refreshed every `DNS_REFRESH_INTERVAL` (30 s). A host
   may list several IPs. If `IDENTITY_SVC_URL` or `INTERNAL_SECRET` is unset,
   only the `DNS_MAPPINGS` env var (JSON) is used.
2. **`*.docker`, container names and container hostnames**: live from the
   Docker socket.
3. **`host.docker.internal`**: the bridge gateway, `172.26.0.1`.
4. **Upstream**: `UPSTREAM_DNS` (compose default `1.1.1.1`; several may be
   comma-separated and are queried at once, first answer wins). Answers are
   cached for their TTL clamped to 30-300 s, "no such name" for 30 s, and an
   answer up to an hour stale stands in when every upstream fails.

Only A records are answered. Any other question type (AAAA included) gets an
immediate empty NOERROR reply, and an A question with no answer gets SERVFAIL.

## Health-checked static mappings

When a mapping lists several IPs and failover is on (`dns_failover_enabled`,
default true), only the IPs that answer are returned:

- Each candidate is probed concurrently (a TCP connect to
  `dns_health_ports`, or an HTTP GET of `dns_health_path` when set;
  `DNS_HEALTH_TIMEOUT` 1 s each).
- Results are cached for `DNS_HEALTH_TTL` (15 s). An expired result is answered
  at once and re-probed in the background (stale-while-revalidate), so a
  lookup only waits on a probe the first time an address is seen.
- These answers carry the health interval as their TTL (at least 5 s), so a
  client follows a switch within one interval instead of holding a dead IP.
- If no candidate answers, the **last address that did** is returned, not the
  whole pool.

Example: `jeremiah-home-desktop.local` (the Ollama / audio box) lists several
addresses; the answer is whichever is up.

## Settings

| Setting | Where | Default |
|---|---|---|
| `dns_mappings` | Identity global setting (JSON: host → IP or list) | `{}` |
| `dns_failover_enabled`, `dns_health_ports`, `dns_health_path` | Identity global settings | on, see below, none |
| `UPSTREAM_DNS` | compose / `.env` | `1.1.1.1` |
| `DNS_HEALTH_PORTS`, `DNS_HEALTH_PATH` | env (the Identity settings above override them) | `11434,80,443,8080,8000,9000`, none |
| `DNS_HEALTH_TIMEOUT`, `DNS_HEALTH_TTL`, `DNS_REFRESH_INTERVAL` | env | `1.0`, `15.0`, `30.0` |
| `DNS_UPSTREAM_TIMEOUT` | env | `5.0` |

## Checking it

```bash
dig A jeremiah-home-desktop.local @172.26.0.254          # on the server
docker exec sharedllm_execution getent hosts jeremiah-home-desktop.local
```

Tests: `services/dns/tests/` (health-aware mappings, upstream timeout and
fallback, IPv4-only answers, Docker watcher reconnect).
