# DNS Sync Service

## Overview

The DNS sync sidecar provides health-aware DNS resolution for `.local` hostnames across the SharedLLM microservice architecture. It serves as the primary DNS resolver for containers on the `sharedllm` Docker network.

## Architecture

### Components

1. **DNS Server** (UDP port 53)
   - Listens for A-record queries on `0.0.0.0:53`
   - Returns only healthy IPs for each hostname
   - Falls back to all IPs if no health check passes
   - Forwards unknown queries to upstream DNS

2. **Health Checker** (interval: 10s)
   - TCP-connects to each configured IP on a service-specific port
   - Updates health status in real-time
   - Triggers `/etc/hosts` sync when health changes

3. **Identity Poller** (interval: 30s)
   - Fetches `dns_mappings` from Identity `/api/settings`
   - Updates DNS records when mappings change
   - Triggers health checks for new entries

4. **Hosts File Sync**
   - Writes alive IPs to host's `/etc/hosts`
   - Mounted volume: `/etc/hosts:/etc/hosts`
   - Only writes first alive IP per hostname

### Configuration

#### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `INTERNAL_SECRET` | (required) | Service-to-service auth token |
| `IDENTITY_SVC_URL` | `http://localhost:8001` | Identity service URL |
| `DNS_POLL_INTERVAL` | 30 | Seconds between Identity polls |
| `DNS_LISTEN_PORT` | 53 | DNS server UDP port |
| `UPSTREAM_DNS` | `127.0.0.11` | Primary upstream DNS |
| `UPSTREAM_DNS_2` | `192.168.1.1` | Secondary upstream DNS |
| `HEALTH_CHECK_INTERVAL` | 10 | Seconds between health checks |
| `HEALTH_CHECK_TIMEOUT` | 2 | TCP connect timeout in seconds |
| `HOSTS_FILE` | `/etc/hosts` | Path to hosts file |
| `HOSTS_SYNC` | `true` | Enable hosts file sync |

#### Health Check Ports

Default health check ports by hostname pattern:

| Pattern | Port |
|---------|------|
| `ollama-server` | 11434 |
| `llama-server` | 11434 |
| `ai` | 8080 |
| `execution` | 8003 |
| Default | 80 |

#### DNS Mappings Format

Stored in Identity `GlobalSettings` as `dns_mappings` (JSON):

```json
{
  "hostname.local": ["ip1", "ip2", "host-gateway"]
}
```

**Special values:**
- `host-gateway`: Dynamically resolved via `/proc/net/route` → Docker bridge gateway
- Multiple IPs for failover (tried in order)

### Docker Configuration

#### Service Definition

```yaml
dns-sync:
  image: ghcr.io/jmiahman1/sharedllm-dns_sync:latest
  container_name: sharedllm_dns_sync
  ports:
    - "127.0.0.1:5354:53/udp"  # External access on 5354
  volumes:
    - /etc/hosts:/etc/hosts
  networks:
    sharedllm:
      ipv4_address: 172.26.0.10  # Fixed IP = DNS nameserver
```

#### Container DNS Configuration

All services (except `ui`, `redis`, `execution`) use:

```yaml
dns:
  - "172.26.0.10"  # DNS sync container
  - "192.168.1.1"  # Router fallback
```

**Host-networked services** (`execution`) use `dns_resolver.py` to patch `socket.getaddrinfo` and route `.local` queries to `127.0.0.1:5353` on the host.

### Resolution Chain

1. **Container DNS** → Queries `172.26.0.10` (dns-sync)
2. **Health Check** → dns-sync returns only alive IPs
3. **Fallback** → If all dead, returns all configured IPs
4. **Unknown** → Forwards to upstream DNS (`127.0.0.11`, then `192.168.1.1`)
5. **Host Network** → `/etc/hosts` sync + `dns_resolver.py` socket patch

## Current Mappings

```json
{
  "ai.local": ["host-gateway"],
  "execution.local": ["host-gateway"],
  "ollama-server.local": ["192.168.2.200", "192.168.2.114", "192.168.4.179", "192.168.1.204"]
}
```

- `192.168.2.200` = Primary Ollama server (Alpaca)
- Fallback IPs for redundancy (health-aware failover)

## Troubleshooting

### DNS Resolution Failing

1. **Check DNS sync container logs:**
   ```bash
   docker logs sharedllm_dns_sync | tail -30
   ```

2. **Verify DNS mappings in Identity:**
   ```bash
   curl -s 'http://localhost:8001/api/settings' \
     -H 'X-Internal-Secret: RAVEN_SECURE_2026'
   ```

3. **Test DNS resolution:**
   ```bash
   # From any container on sharedllm network
   dig @172.26.0.10 ollama-server.local +short
   
   # From host
   dig @127.0.0.1 -p 5354 ollama-server.local +short
   ```

4. **Restart DNS sync:**
   ```bash
   docker restart sharedllm_dns_sync
   ```

### Hosts File Not Updating

- Check `HOSTS_SYNC=true` in environment
- Verify `/etc/hosts:/etc/hosts` volume mount
- Check container has `CAP_NET_ADMIN` capability

### Health Checker Flapping

Health checker alternates DEAD/ALIVE when:
- TCP timeout too aggressive (default 2s)
- Ollama overloaded (slow responses)
- Network latency between containers

**Fix:** Increase `HEALTH_CHECK_TIMEOUT` to 5s:
```yaml
environment:
  - HEALTH_CHECK_TIMEOUT=5
```

## File Structure

| File | Purpose |
|------|---------|
| `config/dns_sync.py` | Main sidecar logic (422 lines) |
| `services/dns_sync/Dockerfile` | Container image |
| `services/dns_sync/entrypoint.sh` | Start dnsmasq + dns_sync.py |
| `services/execution/dns_resolver.py` | Socket patch for host-networked containers |
| `services/dns_entrypoint.sh` | Legacy startup-time hosts writer |

## See Also

- `AGENTS.md` - Critical runtime rules
- `docker-compose.yml` - Service definitions
- `services/identity/models.py` - Default DNS seed data
- `docs/DNS_RESOLVER.md` - DNS resolver patch for host-networked containers
