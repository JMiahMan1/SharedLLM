# DNS Resolver (Host-Networked Services)

## Overview

The DNS resolver patch enables the `execution` service (which uses `network_mode: host`) to resolve `.local` domains via the DNS sync sidecar, providing live failover without restarts.

## Why It's Needed

### Docker DNS Limitation

Host-networked containers (`network_mode: host`) cannot use Docker DNS (`172.26.0.10`). They inherit the host's network stack and DNS configuration, bypassing Docker's embedded DNS server.

### Solution

Patch Python's `socket.getaddrinfo` to route `.local` queries through the dns-sync sidecar running on the host.

## How It Works

### Socket-Level Patching

1. **At startup:** Call `patch_dns_resolver()` to replace `socket.getaddrinfo`
2. **On resolution:** Intercept `.local` hostname lookups
3. **Forward to dns-sync:** Query `127.0.0.1:5353` (dnsmasq proxy on host)
4. **Fallback:** Use original `getaddrinfo` for non-`.local` domains or if resolution fails

### dnsmasq Proxy

The dns-sync container exposes port 5354 on the host, but the execution service connects to port 5353 (dnsmasq on the host). Port 5353 is forwarded to 53 inside the dns-sync container.

## Configuration

### Constants

```python
_DNS_SYNC_IP = "127.0.0.1"  # Host localhost
_DNS_SYNC_PORT = 5353       # dnsmasq port on host
```

### Usage

```python
from services.execution.dns_resolver import patch_dns_resolver

# Call once at startup
patch_dns_resolver()
```

## Supported Hostnames

| Hostname | Port | Purpose |
|----------|------|---------|
| `ollama-server.local` | 11434 | Ollama inference server |
| `ai.local` | 8080 | AI service |
| `execution.local` | 8003 | Execution service (self) |

## Health Awareness

The patch inherits health awareness from the dns-sync sidecar:
- dns-sync health-checks IPs every 10 seconds
- DNS responses contain only alive IPs
- Execution service automatically fails over to next IP on query

## Limitations

1. **Python only:** Only patches Python's socket layer; subprocess calls use host DNS
2. **dnspython required:** Uses `dns.resolver` from dnspython package
3. **Port mismatch:** dns-sync listens on 53, exposed as 5354; dnsmasq uses 5353
4. **Fallback:** If dnspython unavailable, falls back to system DNS (no health awareness)

## Troubleshooting

### Resolution Failing

1. **Check dnspython is installed:**
   ```bash
   docker exec sharedllm_execution python -c "import dns.resolver; print('OK')"
   ```

2. **Verify dnsmasq is running:**
   ```bash
   curl -s http://127.0.0.1:5353 2>&1 | head -5
   ```

3. **Check execution service logs:**
   ```bash
   docker logs sharedllm_execution 2>&1 | grep -i dns
   ```

4. **Test resolution manually:**
   ```bash
   # From host
   dig @127.0.0.1 -p 5354 ollama-server.local +short
   ```

### Patch Not Applied

- Verify `patch_dns_resolver()` is called at startup
- Check for import errors in execution service logs
- Ensure `network_mode: host` in docker-compose.yml

## File Structure

| File | Purpose |
|------|---------|
| `services/execution/dns_resolver.py` | Socket patch implementation (49 lines) |
| `config/dns_sync.py` | DNS sync sidecar (422 lines) |
| `docker-compose.yml` | Service definitions |

## See Also

- `docs/DNS_SYNC_SERVICE.md` - DNS sync sidecar documentation
- `services/execution/` - Execution service code
- `AGENTS.md` - Critical runtime rules
