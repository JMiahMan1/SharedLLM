# services/execution/device_discovery.py
"""
Multi-strategy device network discovery module.

Discovers IP, MAC, and hostname for HA entities using ordered strategies:
1. Persistent registry cache (aiosqlite, instant)
2. HA device registry (REST API) + ESPHome config entries
3. HA entity attributes (some integrations expose IP/MAC directly)
4. ARP table scan (requires host network mode)
5. mDNS/Bonjour resolution (.local hostnames from entity_id)
6. SSDP broadcast (Roku, DLNA, Chromecast)
7. Batched network port scan (fallback, slowest)

Each strategy is independent and can be called individually or as a pipeline.
Discovered info is automatically persisted to the device registry.
"""
import asyncio
import contextlib
import ipaddress
import logging
import os
import socket
from typing import cast

try:
    from . import device_registry as _device_registry_mod
    from . import ha_client as _ha_client_mod
except ImportError:
    _device_registry_mod = None  # type: ignore[no-redef, assignment]
    _ha_client_mod = None  # type: ignore[no-redef, assignment]

# Backwards compat aliases
device_registry = _device_registry_mod
ha_client = _ha_client_mod

log = logging.getLogger("execution.discovery")


def get_local_subnet() -> str:
    """Auto-detect the local subnet from host network interfaces."""
    # Try env var first
    env_subnet = os.environ.get("SCAN_SUBNET") or os.environ.get("LOCAL_SUBNET")
    if env_subnet:
        return env_subnet

    # Try to get from routing table
    try:
        with open("/proc/net/route") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 3 and parts[1] == "00000000":
                    iface = parts[0]
                    # Get IP for this interface
                    result = os.popen(f"ip -j addr show {iface}").read()
                    import json
                    data = json.loads(result)
                    for addr_info in data[0].get("addr_info", []):
                        if addr_info.get("family") == "inet":
                            ip = addr_info.get("local")
                            prefix = addr_info.get("prefixlen", 24)
                            network = ipaddress.IPv4Network(f"{ip}/{prefix}", strict=False)
                            return str(network)
    except Exception:
        pass

    # Fallback: get IP from socket and assume /24
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
        return str(network)
    except Exception:
        # Guessing here scanned a network that is usually not this one, so the
        # scan silently found nothing. Say what is missing instead.
        raise RuntimeError(
            "Could not determine this host's subnet, so device discovery has "
            "nothing to scan. Set SCAN_SUBNET (or LOCAL_SUBNET) to the /24 to "
            "scan, e.g. 192.168.2.0/24, or fix the host's networking."
        ) from None


DEFAULT_SUBNET = get_local_subnet()


def _get_local_self_ips() -> set[str]:
    """Return local host IPs so port scanning never misidentifies the SharedLLM server as a TV."""
    ips = {"127.0.0.1", "0.0.0.0"}
    ext_host = (os.environ.get("EXECUTION_EXTERNAL_HOST") or "").strip()
    if ext_host:
        ips.add(ext_host)
    with contextlib.suppress(Exception):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    return ips


DEVICE_PORTS = {
    "roku": [(8060, "roku:ecp")],
    "webos": [(3000, "webos"), (3001, "webos-wss"), (9080, "webos-status"), (7676, "webos")],
    "samsung": [(8001, "samsung"), (8002, "samsung-wss"), (55000, "samsung-legacy"), (7676, "samsung-dlna")],
    "androidtv": [(6466, "androidtv-remote"), (5555, "adb"), (8008, "eureka")],
    "cast": [(8008, "eureka"), (8009, "cast")],
    "dlna": [(9197, "dlna"), (8200, "dlna")],
    "esphome": [(6053, "esphome-api"), (80, "esphome"), (8080, "espcam")],
    "tasmota": [(80, "tasmota")],
    "mqtt": [(1883, "mqtt"), (8883, "mqtt-tls")],
}


async def discover_device(
    entity_id: str,
    ha_url: str,
    ha_token: str,
    device_type: str | None = None,
    subnet: str = DEFAULT_SUBNET,
    use_cache: bool = True,
) -> dict | None:
    """Full discovery pipeline. Returns device info dict or None."""
    if use_cache:
        cached = await device_registry.get_device(entity_id)
        if cached and cached.get("ip") and not cached.get("ip_stale"):
            log.info(f"[discovery] Cache hit for {entity_id}: {cached['ip']}")
            return cached

    result = await _discover_via_ha_registry(entity_id, ha_url, ha_token, device_type)
    if result:
        return result

    result = await _discover_via_homekit_diagnostics(entity_id, ha_url, ha_token)
    if result:
        return result

    result = await _discover_via_entity_attrs(entity_id, ha_url, ha_token)
    if result:
        return result

    result = await _discover_via_arp(entity_id, ha_url, ha_token)
    if result:
        return result

    result = await _discover_via_arp_scan(entity_id, ha_url, ha_token)
    if result:
        return result

    result = await _discover_via_snmp(entity_id, ha_url, ha_token)
    if result:
        return result

    result = await _discover_via_mdns(entity_id, ha_url, ha_token)
    if result:
        return result

    result = await _discover_via_ssdp(entity_id, device_type)
    if result:
        return result

    result = await _discover_via_network_scan(entity_id, ha_url, ha_token, device_type, subnet)
    if result:
        return result

    log.warning(f"[discovery] All strategies failed for {entity_id}")
    return None


async def _discover_via_ha_registry(
    entity_id: str, ha_url: str, ha_token: str, device_type: str | None = None
) -> dict | None:
    """Look up IP/MAC via HA device registry REST API."""
    import aiohttp
    try:
        headers = {"Authorization": f"Bearer {ha_token}"}
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if not state:
            return None

        attrs = state.get("attributes", {})
        friendly_name = attrs.get("friendly_name", "")

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), connector=aiohttp.TCPConnector(ssl=False)) as client:
            resp = await client.get(f"{ha_url}/api/config/config_entries/entry", headers=headers)
            if resp.status != 200:
                return None

            entries = await resp.json()
            device_registry_list = None

            for entry in entries:
                domain = entry.get("domain", "")
                title = (entry.get("title") or "").lower()
                entity_lower = entity_id.lower()
                friendly_lower = friendly_name.lower()

                if device_type and domain != device_type:
                    continue

                name_match = (
                    any(part in title for part in entity_lower.split(".") if len(part) > 2) or
                    any(part in title for part in friendly_lower.split() if len(part) > 2)
                )
                if not name_match:
                    continue

                host = entry.get("data", {}).get("host") or entry.get("options", {}).get("host")
                if not host:
                    continue

                mac = None
                if device_registry_list is None:
                    try:
                        dev_resp = await client.get(f"{ha_url}/api/config/device_registry/list", headers=headers)
                        if dev_resp.status == 200:
                            device_registry_list = await dev_resp.json()
                    except Exception:
                        device_registry_list = []

                if device_registry_list:
                    for dev in device_registry_list:
                        config_entries = dev.get("config_entries", [])
                        if entry["entry_id"] in config_entries:
                            for conn in dev.get("connections", []):
                                if conn and conn[0] == "mac":
                                    mac = conn[1]
                                    break
                        if mac:
                            break

                hostname = None
                with contextlib.suppress(Exception):
                    hostname = socket.gethostbyaddr(host)[0]

                await device_registry.set_device(
                    entity_id,
                    ip=host,
                    mac=mac,
                    hostname=hostname,
                    friendly_name=friendly_name,
                    integration=domain,
                    discovery_method="ha_registry",
                )
                log.info(f"[discovery] Found {entity_id} via HA registry: ip={host} mac={mac}")
                return await device_registry.get_device(entity_id)
    except Exception as e:
        log.warning(f"[discovery] HA registry lookup failed: {e}")

    # ESPHome-specific: check config entries for host
    if device_type == "esphome" or not device_type:
        try:
            import aiohttp
            headers = {"Authorization": f"Bearer {ha_token}"}
            state = await ha_client.get_state(ha_url, ha_token, entity_id)
            if not state:
                return None
            friendly_name = state.get("attributes", {}).get("friendly_name", "")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), connector=aiohttp.TCPConnector(ssl=False)) as client:
                resp = await client.get(f"{ha_url}/api/config/config_entries/entry", headers=headers)
                if resp.status == 200:
                    for entry in await resp.json():
                        if entry.get("domain") == "esphome":
                            title = (entry.get("title") or "").lower()
                            entity_lower = entity_id.lower()
                            friendly_lower = friendly_name.lower()
                            if any(part in title for part in entity_lower.split(".") if len(part) > 2) or \
                               any(part in title for part in friendly_lower.split() if len(part) > 2):
                                host = entry.get("data", {}).get("host")
                                if host:
                                    await device_registry.set_device(
                                        entity_id,
                                        ip=host,
                                        friendly_name=friendly_name,
                                        integration="esphome",
                                        discovery_method="ha_esphome_config",
                                    )
                                    log.info(f"[discovery] Found {entity_id} via ESPHome config: ip={host}")
                                    return await device_registry.get_device(entity_id)
        except Exception as e:
            log.warning(f"[discovery] ESPHome config lookup failed: {e}")

    return None


async def _discover_via_homekit_diagnostics(
    entity_id: str, ha_url: str, ha_token: str
) -> dict | None:
    """Get IP from HomeKit controller diagnostics for WebOS TVs.

    WebOS config entries have host/IP redacted in REST API, but HomeKit
    controller diagnostics expose AccessoryIPs. This bridges the gap by
    matching webostv devices to their HomeKit sibling entries.
    """
    import aiohttp
    try:
        headers = {"Authorization": f"Bearer {ha_token}"}
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if not state:
            return None

        friendly_name = state.get("attributes", {}).get("friendly_name", "")
        entity_lower = entity_id.lower()
        friendly_lower = friendly_name.lower()

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), connector=aiohttp.TCPConnector(ssl=False)) as client:
            # Get device registry to find webostv device info
            dev_resp = await client.get(f"{ha_url}/api/config/device_registry/list", headers=headers)
            if dev_resp.status != 200:
                return None
            device_registry_list = await dev_resp.json()

            # Find the webostv device for this entity
            webos_device = None
            for dev in device_registry_list:
                dev_name = (dev.get("name") or "").lower()
                dev_name_by_user = (dev.get("name_by_user") or "").lower()
                (dev.get("model") or "").lower()
                if (any(part in dev_name for part in friendly_lower.split() if len(part) > 2) or
                    any(part in dev_name_by_user for part in friendly_lower.split() if len(part) > 2) or
                    any(part in dev_name for part in entity_lower.split(".") if len(part) > 2)):
                    # Check if it's a webostv device
                    for identifier in dev.get("identifiers", []):
                        if identifier and identifier[0] == "webostv":
                            webos_device = dev
                            break
                if webos_device:
                    break

            if not webos_device:
                return None

            webos_model = (webos_device.get("model") or "").lower()
            webos_manufacturer = (webos_device.get("manufacturer") or "").lower()
            (webos_device.get("serial_number") or "").lower()

            # Find matching homekit_controller config entries
            entries_resp = await client.get(f"{ha_url}/api/config/config_entries/entry", headers=headers)
            if entries_resp.status != 200:
                return None

            for entry in await entries_resp.json():
                if entry.get("domain") != "homekit_controller":
                    continue

                entry_id = entry.get("entry_id")
                if not entry_id:
                    continue

                # Call diagnostics endpoint
                diag_resp = await client.get(f"{ha_url}/api/diagnostics/config_entry/{entry_id}", headers=headers)
                if diag_resp.status != 200:
                    continue

                diag_data = await diag_resp.json()
                config_entry_data = diag_data.get("data", {}).get("config-entry", {})
                accessory_data = config_entry_data.get("data", {})
                accessory_ips = accessory_data.get("AccessoryIPs", [])

                if not accessory_ips:
                    continue

                # Match by model or manufacturer
                diag_model = (config_entry_data.get("model") or "").lower()
                diag_manufacturer = (config_entry_data.get("manufacturer") or "").lower()
                diag_title = (config_entry_data.get("title") or "").lower()

                model_match = webos_model and webos_model in diag_model
                manufacturer_match = webos_manufacturer and (
                    webos_manufacturer in diag_manufacturer or
                    diag_manufacturer in webos_manufacturer
                )
                title_match = any(
                    part in diag_title for part in friendly_lower.split() if len(part) > 2
                )

                if model_match or manufacturer_match or title_match:
                    ip = accessory_ips[0]
                    await device_registry.set_device(
                        entity_id,
                        ip=ip,
                        friendly_name=friendly_name,
                        integration="webostv",
                        discovery_method="homekit_diagnostics",
                    )
                    log.info(f"[discovery] Found {entity_id} via HomeKit diagnostics: ip={ip}")
                    return await device_registry.get_device(entity_id)
    except Exception as e:
        log.warning(f"[discovery] HomeKit diagnostics lookup failed: {e}")
    return None


async def _discover_via_entity_attrs(
    entity_id: str, ha_url: str, ha_token: str
) -> dict | None:
    """Extract IP/MAC from entity attributes."""
    try:
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if not state:
            return None

        attrs = state.get("attributes", {})
        ip = attrs.get("ip_address") or attrs.get("ip") or attrs.get("host")
        mac = attrs.get("mac_address") or attrs.get("mac")

        if ip or mac:
            await device_registry.set_device(
                entity_id,
                ip=ip,
                mac=mac,
                friendly_name=attrs.get("friendly_name", ""),
                integration=attrs.get("integration", ""),
                device_class=attrs.get("device_class", ""),
                discovery_method="entity_attributes",
            )
            log.info(f"[discovery] Found {entity_id} via entity attrs: ip={ip} mac={mac}")
            return await device_registry.get_device(entity_id)
    except Exception as e:
        log.warning(f"[discovery] Entity attr lookup failed: {e}")
    return None


async def _discover_via_arp(
    entity_id: str, ha_url: str, ha_token: str
) -> dict | None:
    """Get IPs from ARP table, probe for device ports, match by device info."""
    try:
        import subprocess

        import aiohttp
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if not state:
            return None

        friendly = state.get("attributes", {}).get("friendly_name", "").lower()
        entity_base = entity_id.split(".")[-1].lower().replace("_", " ")

        # Get IPs from ARP table
        arp_ips = []
        try:
            with open("/proc/net/arp") as f:
                lines = f.read().splitlines()[1:]  # skip header
            for line in lines:
                parts = line.split()
                if len(parts) >= 4:
                    ip_part = parts[0]
                    mac_part = parts[3]
                    if mac_part != "00:00:00:00:00:00" and mac_part != "incomplete":
                        arp_ips.append((ip_part, mac_part))
        except FileNotFoundError:
            result = await asyncio.to_thread(subprocess.run, ["arp", "-a"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 4:
                        ip_part = parts[1].strip("()")
                        mac_part = parts[3]
                        if mac_part != "00:00:00:00:00:00" and mac_part != "incomplete":
                            arp_ips.append((ip_part, mac_part))

        if not arp_ips:
            return None

        # Probe ARP IPs for WebOS ports
        webos_ports = [3000, 7676, 1300, 8080]
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2), connector=aiohttp.TCPConnector(ssl=False)) as client:
            for ip, mac in arp_ips:
                for port in webos_ports:
                    try:
                        resp = await client.get(f"http://{ip}:{port}", timeout=aiohttp.ClientTimeout(total=1))
                        if resp.status == 200:
                            # Check if device info matches entity
                            body = (await resp.text()).lower()
                            searchable = f"{body} {ip} {mac}".lower()
                            if any(word in searchable for word in friendly.split() if len(word) > 2) or \
                               any(word in searchable for word in entity_base.split() if len(word) > 2) or \
                               "lg" in searchable or "webos" in searchable or "living" in searchable:
                                await device_registry.set_device(
                                    entity_id,
                                    ip=ip,
                                    mac=mac,
                                    friendly_name=state["attributes"].get("friendly_name", ""),
                                    discovery_method="arp",
                                )
                                log.info(f"[discovery] Found {entity_id} via ARP+port scan: ip={ip} mac={mac}")
                                return await device_registry.get_device(entity_id)
                    except Exception:
                        pass
    except Exception as e:
        log.warning(f"[discovery] ARP lookup failed: {e}")
    return None


async def _discover_via_arp_scan(
    entity_id: str, ha_url: str, ha_token: str
) -> dict | None:
    """Scan subnets with arp-scan, probe for device ports, match by device info."""
    try:
        import subprocess

        import aiohttp
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if not state:
            return None

        friendly = state.get("attributes", {}).get("friendly_name", "").lower()
        entity_base = entity_id.split(".")[-1].lower().replace("_", " ")

        # Scan common subnets (local + adjacent)
        local_subnet = get_local_subnet()
        # Also scan adjacent /24 in case device moved subnets
        try:
            net = ipaddress.IPv4Network(local_subnet, strict=False)
            subnets = [local_subnet]
            # Add adjacent subnet (e.g., if on 192.168.2.0/24, also scan 192.168.1.0/24)
            adjacent = list(net.network_address.packed)
            adjacent[2] = (adjacent[2] + 1) % 256
            subnets.append(f"{adjacent[0]}.{adjacent[1]}.{adjacent[2]}.0/24")
        except Exception:
            subnets = [local_subnet]
        found_devices = []

        for subnet in subnets:
            try:
                result = await asyncio.to_thread(subprocess.run,
                    ["arp-scan", "--localnet", "--interface=wlo1", f"--net={subnet}"],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    for line in result.stdout.splitlines()[1:]:  # skip header
                        parts = line.split()
                        if len(parts) >= 3:
                            ip_part = parts[0]
                            mac_part = parts[1]
                            vendor = " ".join(parts[2:]).lower() if len(parts) > 2 else ""
                            if mac_part != "00:00:00:00:00:00":
                                found_devices.append((ip_part, mac_part, vendor))
            except subprocess.TimeoutExpired:
                continue
            except Exception:
                continue

        if not found_devices:
            return None

        # Probe found IPs for WebOS ports
        webos_ports = [3000, 7676, 1300, 8080]
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2), connector=aiohttp.TCPConnector(ssl=False)) as client:
            for ip, mac, vendor in found_devices:
                for port in webos_ports:
                    try:
                        resp = await client.get(f"http://{ip}:{port}", timeout=aiohttp.ClientTimeout(total=1))
                        if resp.status == 200:
                            body = (await resp.text()).lower()
                            searchable = f"{body} {ip} {mac} {vendor}".lower()
                            if any(word in searchable for word in friendly.split() if len(word) > 2) or \
                               any(word in searchable for word in entity_base.split() if len(word) > 2) or \
                               "lg" in searchable or "webos" in searchable or "living" in searchable:
                                await device_registry.set_device(
                                    entity_id,
                                    ip=ip,
                                    mac=mac,
                                    friendly_name=state["attributes"].get("friendly_name", ""),
                                    discovery_method="arp_scan",
                                )
                                log.info(f"[discovery] Found {entity_id} via arp-scan: ip={ip} mac={mac}")
                                return await device_registry.get_device(entity_id)
                    except Exception:
                        pass
    except Exception as e:
        log.warning(f"[discovery] arp-scan lookup failed: {e}")
    return None


async def _discover_via_snmp(
    entity_id: str,
    ha_url: str,
    ha_token: str,
    router_ip: str | None = None,
    community: str = "public",
) -> dict | None:
    """Get MAC/IP from router ARP table via SNMP walk.

    The router used to default to one install's gateway (192.168.2.1), so on any
    other network the walk targeted a host that is not a router. ROUTER_IP is the
    configured answer; without it this strategy declines rather than walking a
    stranger's router, and the other discovery paths still run.
    """
    router_ip = (router_ip or os.environ.get("ROUTER_IP") or "").strip()
    if not router_ip:
        log.info(
            "[discovery] SNMP router lookup skipped: ROUTER_IP is not set "
            "(the other discovery paths still run)."
        )
        return None
    try:
        import subprocess
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if not state:
            return None

        friendly = state.get("attributes", {}).get("friendly_name", "").lower()
        entity_base = entity_id.split(".")[-1].lower().replace("_", " ")

        # SNMP OID: ipNetToMediaPhysAddress (1.3.6.1.2.1.4.22.1.2)
        # Returns: ifIndex.IP.OCTET = MAC address
        result = await asyncio.to_thread(subprocess.run,
            ["snmpwalk", "-v2c", "-c", community, router_ip, "1.3.6.1.2.1.4.22.1.2"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return None

        snmp_entries = []
        for line in result.stdout.splitlines():
            # Format: iso.3.6.1.2.1.4.22.1.2.<ifIndex>.<ip_octets> = Hex-STRING: <mac_bytes>
            parts = line.split()
            if len(parts) >= 4 and parts[-2] == "=":
                oid_part = parts[0]
                mac_bytes = parts[-1]
                # Extract IP from OID (last 4 numbers)
                oid_nums = oid_part.replace("iso.3.6.1.2.1.4.22.1.2.", "").split(".")
                if len(oid_nums) >= 5:
                    ip_part = ".".join(oid_nums[-4:])
                    # Parse MAC from hex string
                    mac_hex = mac_bytes.replace(":", "")
                    if len(mac_hex) == 12:
                        mac_part = ":".join(mac_hex[i:i+2] for i in range(0, 12, 2))
                        snmp_entries.append((ip_part, mac_part))

        if not snmp_entries:
            return None

        # Match by friendly name or entity name in hostname (via reverse DNS)
        import socket
        for ip, mac in snmp_entries:
            hostname = ""
            with contextlib.suppress(Exception):
                hostname = socket.gethostbyaddr(ip)[0].lower()
            searchable = f"{hostname} {ip} {mac}".lower()
            if (any(word in searchable for word in friendly.split() if len(word) > 2) or
                any(word in searchable for word in entity_base.split() if len(word) > 2)):
                await device_registry.set_device(
                    entity_id,
                    ip=ip,
                    mac=mac,
                    hostname=hostname,
                    friendly_name=state["attributes"].get("friendly_name", ""),
                    discovery_method="snmp",
                )
                log.info(f"[discovery] Found {entity_id} via SNMP: ip={ip} mac={mac}")
                return await device_registry.get_device(entity_id)
    except Exception as e:
        log.warning(f"[discovery] SNMP lookup failed: {e}")
    return None


async def _discover_via_mdns(
    entity_id: str, ha_url: str, ha_token: str
) -> dict | None:
    """Resolve hostname via mDNS (.local domain)."""
    try:
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if not state:
            return None

        friendly = state.get("attributes", {}).get("friendly_name", "")
        candidates = []
        if friendly:
            candidates.append(friendly.lower().replace(" ", "").replace("'", "") + ".local")
        entity_base = entity_id.split(".")[-1]
        candidates.append(entity_base + ".local")

        for hostname in candidates:
            try:
                ip = socket.gethostbyname(hostname)
                await device_registry.set_device(
                    entity_id,
                    ip=ip,
                    hostname=hostname.removesuffix(".local"),
                    friendly_name=friendly,
                    discovery_method="mdns",
                )
                log.info(f"[discovery] Found {entity_id} via mDNS: {hostname} -> {ip}")
                return await device_registry.get_device(entity_id)
            except socket.gaierror:
                continue
    except Exception as e:
        log.warning(f"[discovery] mDNS lookup failed: {e}")
    return None


async def _discover_via_ssdp(
    entity_id: str, device_type: str | None = None
) -> dict | None:
    """Discover device via SSDP broadcast."""
    try:
        import socket as sock_module

        search_targets = ["roku:ecp", "urn:dial-multiscreen-org:service:dial:1", "ssdp:all"]
        if device_type == "cast":
            search_targets = ["urn:dial-multiscreen-org:service:dial:1", "ssdp:all"]
        elif device_type == "dlna":
            search_targets = ["urn:schemas-upnp-org:device:MediaRenderer:1", "ssdp:all"]

        for target in search_targets:
            try:
                sock = sock_module.socket(sock_module.AF_INET, sock_module.SOCK_DGRAM)
                sock.settimeout(3)
                sock.bind(("", 0))
                sock.setsockopt(sock_module.SOL_SOCKET, sock_module.SO_BROADCAST, 1)
                request = (
                    f"M-SEARCH * HTTP/1.1\r\n"
                    f"HOST: 239.255.255.250:1900\r\n"
                    f"MAN: \"ssdp:discover\"\r\n"
                    f"MX: 2\r\n"
                    f"ST: {target}\r\n"
                    f"\r\n"
                )
                sock.sendto(request.encode(), ("239.255.255.250", 1900))
                while True:
                    try:
                        data, addr = sock.recvfrom(4096)
                        if (device_type == "roku" or not device_type) and b"Roku" in data:
                            sock.close()
                            await device_registry.set_device(
                                entity_id,
                                ip=addr[0],
                                discovery_method="ssdp",
                            )
                            log.info(f"[discovery] Found {entity_id} via SSDP (Roku): {addr[0]}")
                            return await device_registry.get_device(entity_id)
                    except TimeoutError:
                        break
                sock.close()
            except Exception:
                continue
    except Exception as e:
        log.warning(f"[discovery] SSDP failed: {e}")
    return None


async def _discover_via_network_scan(
    entity_id: str, ha_url: str, ha_token: str, device_type: str | None = None, subnet: str = DEFAULT_SUBNET
) -> dict | None:
    """Scan subnet for device by probing known ports.

    Only returns a match if the probed device info correlates with the entity
    (friendly name, model, or serial match). Prevents all entities mapping to
    the first responding IP on the subnet.
    """
    import ipaddress

    import aiohttp

    try:
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        friendly = ""
        if state:
            friendly = state.get("attributes", {}).get("friendly_name", "").lower()
        entity_base = entity_id.split(".")[-1].lower().replace("_", " ")

        if device_type and device_type in DEVICE_PORTS:
            ports = DEVICE_PORTS[device_type]
        else:
            ports = [(8060, "roku"), (3000, "webos"), (8001, "samsung"), (5555, "adb"), (8009, "cast")]

        def _name_matches(probe_info: dict) -> bool:
            """Check if probed device info correlates with the target entity."""
            metadata = probe_info.get("metadata", {})
            model = (metadata.get("model") or "").lower()
            serial = (metadata.get("serial") or "").lower()
            device_name = (metadata.get("device_name") or "").lower()
            device_type = (metadata.get("type") or "").lower()

            if not friendly and not entity_base:
                return True

            searchable = f"{model} {serial} {device_name}".lower()
            for word in friendly.split():
                if len(word) > 2 and word in searchable:
                    return True
            for word in entity_base.split():
                if len(word) > 2 and word in searchable:
                    return True

            return bool(device_type and device_type in entity_id.lower())

        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as client:
            candidates = [
                str(ip) for ip in ipaddress.IPv4Network(subnet)
                if not str(ip).endswith(".0") and not str(ip).endswith(".255")
            ]
            batch_size = 30
            for i in range(0, len(candidates), batch_size):
                batch = candidates[i:i + batch_size]
                tasks = []
                task_map = []
                for ip in batch:
                    for port, _ in ports:
                        tasks.append(_probe_port(client, ip, port))
                        task_map.append(ip)
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for (ip, port), resp in zip(zip(task_map, [p for _, p in ports] * len(batch), strict=False), results, strict=False):  # type: ignore[assignment, misc]
                    if isinstance(resp, dict) and resp.get("ip"):
                        if _name_matches(resp):
                            await device_registry.set_device(
                                entity_id,
                                ip=ip,
                                friendly_name=friendly,
                                discovery_method="network_scan",
                                metadata=resp.get("metadata", {}),
                            )
                            log.info(f"[discovery] Found {entity_id} via network scan: {ip}")
                            return await device_registry.get_device(entity_id)
                        else:
                            log.debug(f"[discovery] Network scan found {ip} but name mismatch for {entity_id} (friendly='{friendly}')")
    except Exception as e:
        log.warning(f"[discovery] Network scan failed: {e}")
    return None


async def _probe_port(client, ip: str, port: int) -> dict:
    """Probe a single IP:port and return device info with TV OS version & communication methods."""
    if ip in _get_local_self_ips():
        return {}
    try:
        if port == 8060:
            resp = await client.get(f"http://{ip}:8060/query/device-info", timeout=1.5)
            raw = await resp.read() if hasattr(resp, "read") else getattr(resp, "content", b"")
            if isinstance(raw, bytes) and resp.status == 200 and b"roku" in raw.lower():
                import xml.etree.ElementTree as ET
                try:
                    root = ET.fromstring(raw)
                    serial = root.findtext("serial-number", "")
                    model = root.findtext("model-name", "")
                    model_number = root.findtext("model-number", "")
                    vendor = root.findtext("vendor-name", "")
                    sw_ver = root.findtext("software-version", "")
                    sw_build = root.findtext("software-build", "")
                    device_name = root.findtext("user-device-name", "") or root.findtext("friendly-device-name", "")
                    power_mode = root.findtext("power-mode", "")
                    is_tv = root.findtext("is-tv", "false") == "true"
                    supports_mic = root.findtext("supports-ecs-microphone", "false") == "true"
                    mac = root.findtext("wifi-mac", "") or root.findtext("ethernet-mac", "")
                    has_media_assistant = False
                    has_roku_media_player = False
                    try:
                        apps_resp = await client.get(f"http://{ip}:8060/query/apps", timeout=1.0)
                        apps_raw = await apps_resp.read() if hasattr(apps_resp, "read") else b""
                        if apps_resp.status == 200 and apps_raw:
                            apps_root = ET.fromstring(apps_raw)
                            for app_el in apps_root.findall("app"):
                                aid = app_el.attrib.get("id", "")
                                if aid == "782875":
                                    has_media_assistant = True
                                elif aid == "2213":
                                    has_roku_media_player = True
                    except Exception:
                        pass
                    comm_methods = ["ecp_keypress", "music_assistant", "ha_media_player"]
                    if has_media_assistant:
                        comm_methods.insert(0, "ecp_media_assistant_782875")
                    return {
                        "ip": ip,
                        "metadata": {
                            "serial": serial,
                            "model": model,
                            "model_number": model_number,
                            "vendor": vendor,
                            "device_name": device_name,
                            "software_version": sw_ver,
                            "os_version": f"Roku OS {sw_ver} (build {sw_build})".strip() if sw_ver else "Roku OS",
                            "power_mode": power_mode,
                            "is_tv": is_tv,
                            "supports_ecs_microphone": supports_mic,
                            "mac": mac,
                            "has_media_assistant": has_media_assistant,
                            "has_roku_media_player": has_roku_media_player,
                            "communication_methods": comm_methods,
                            "type": "roku",
                        },
                    }
                except ET.ParseError:
                    return {"ip": ip, "metadata": {"type": "roku", "os_version": "Roku OS"}}
        elif port in (3000, 3001, 9080):
            if port == 9080:
                resp = await client.get(f"http://{ip}:9080/", timeout=1.2)
                if resp.status == 200:
                    txt = (await resp.text()).lower()
                    if "status=ok" in txt:
                        return {
                            "ip": ip,
                            "metadata": {
                                "type": "webos",
                                "os_version": "LG webOS",
                                "communication_methods": ["ha_notify_toast", "webostv_command_toast", "ha_play_media", "music_assistant"],
                            },
                        }
            else:
                resp = await client.get(f"http://{ip}:{port}", timeout=1.2)
                if resp.status == 200:
                    txt = (await resp.text()).lower()
                    # Exclude standard HTML web servers (e.g. OpenWebUI on port 3000)
                    if "<!doctype html" not in txt and "<html" not in txt and "open-webui" not in txt:
                        return {
                            "ip": ip,
                            "metadata": {
                                "type": "webos",
                                "os_version": "LG webOS",
                                "communication_methods": ["ha_notify_toast", "webostv_command_toast", "ha_play_media", "music_assistant"],
                            },
                        }
        elif port in (8001, 8002, 55000):
            if port == 55000:
                try:
                    _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=1.0)
                    writer.close()
                    await writer.wait_closed()
                    return {
                        "ip": ip,
                        "metadata": {
                            "type": "samsung",
                            "generation": "legacy_orsay",
                            "os_version": "Samsung Orsay (Legacy)",
                            "communication_methods": ["ha_samsungtv_legacy", "dlna_dmr", "room_cast_companion"],
                        },
                    }
                except Exception:
                    pass
            else:
                scheme = "https" if port == 8002 else "http"
                resp = await client.get(f"{scheme}://{ip}:{port}/api/v2/", timeout=1.5)
                if resp.status == 200:
                    try:
                        info = await resp.json()
                        dev = info.get("device") if isinstance(info, dict) else None
                        if isinstance(dev, dict) and (dev.get("modelName") or dev.get("OS") or dev.get("type")):
                            os_name = dev.get("OS") or "Tizen"
                            sw_ver = info.get("version") or dev.get("firmwareVersion") or ""
                            return {
                                "ip": ip,
                                "metadata": {
                                    "model": dev.get("modelName", ""),
                                    "device_name": info.get("name") or dev.get("name", ""),
                                    "os_version": f"Samsung {os_name} {sw_ver}".strip(),
                                    "generation": "tizen",
                                    "voice_support": dev.get("VoiceSupport") == "true",
                                    "mac": dev.get("wifiMac", ""),
                                    "supports_wol": dev.get("supportWOL") == "true" or bool(dev.get("supportWOL")),
                                    "communication_methods": ["music_assistant", "dlna_dmr", "ha_play_media", "samsungtv_websocket"],
                                    "type": "samsung",
                                },
                            }
                    except Exception:
                        pass
        elif port in (8008, 8009, 6466, 5555):
            # Probe Google Cast / Android TV eureka_info on port 8008 first
            eureka = None
            with contextlib.suppress(Exception):
                resp = await client.get(
                    f"http://{ip}:8008/setup/eureka_info?params=version,name,build_info,device_info,net,wifi,audio",
                    timeout=1.5,
                )
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict) and (data.get("cast_build_revision") or data.get("ssdp_udn")):
                        eureka = data

            has_6466 = False
            has_5555 = False
            for check_p in (6466, 5555):
                try:
                    _, writer = await asyncio.wait_for(asyncio.open_connection(ip, check_p), timeout=0.6)
                    writer.close()
                    await writer.wait_closed()
                    if check_p == 6466:
                        has_6466 = True
                    elif check_p == 5555:
                        has_5555 = True
                except Exception:
                    pass

            if eureka or has_6466 or has_5555:
                is_android_tv = has_6466 or has_5555
                cast_rev = (eureka or {}).get("cast_build_revision", "")
                dev_name = (eureka or {}).get("name", "")
                mac = (eureka or {}).get("mac_address", "")
                udn = (eureka or {}).get("ssdp_udn", "")
                os_label = f"Android TV (Cast {cast_rev})" if (is_android_tv and cast_rev) else (
                    "Android TV" if is_android_tv else (f"Cast {cast_rev}" if cast_rev else "Google Cast")
                )
                comm = ["cast_sibling", "music_assistant", "ha_play_media"]
                if has_6466:
                    comm.insert(0, "androidtv_remote")
                if has_5555:
                    comm.append("adb")
                return {
                    "ip": ip,
                    "metadata": {
                        "device_name": dev_name,
                        "cast_version": cast_rev,
                        "os_version": os_label,
                        "mac": mac,
                        "serial": udn,
                        "has_androidtv_remote": has_6466,
                        "has_adb": has_5555,
                        "communication_methods": comm,
                        "type": "androidtv" if is_android_tv else "cast",
                    },
                }
            elif port == 8009:
                try:
                    _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=0.8)
                    writer.close()
                    await writer.wait_closed()
                    return {"ip": ip, "metadata": {"type": "cast", "os_version": "Google Cast"}}
                except Exception:
                    pass
        elif port == 6053:
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=0.8)
                writer.close()
                await writer.wait_closed()
                return {"ip": ip, "metadata": {"type": "esphome", "os_version": "ESPHome Native API"}}
            except Exception:
                pass
        elif port == 80:
            resp = await client.get(f"http://{ip}/", timeout=1)
            if resp.status == 200:
                html = await resp.text()
                content = html.lower()
                import re
                name_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE)
                device_name = name_match.group(1) if name_match else ""
                if "tasmota" in content:
                    return {"ip": ip, "metadata": {"device_name": device_name, "type": "tasmota"}}
                if "esphome" in content or "<esp-app" in content or "kaufha" in content:
                    return {"ip": ip, "metadata": {"device_name": device_name, "type": "esphome"}}
        elif port == 8080:
            resp = await client.get(f"http://{ip}:8080/", timeout=1)
            if resp.status == 200:
                content = (await resp.text()).lower()
                if "esphome" in content or "espcam" in content or "mjpeg" in content:
                    return {"ip": ip, "metadata": {"type": "espcam"}}
            try:
                stream_resp = await client.get(f"http://{ip}:8080/stream", timeout=1)
                if stream_resp.status == 200:
                    return {"ip": ip, "metadata": {"type": "espcam", "has_stream": True}}
            except Exception:
                pass
        elif port == 1883:
            try:
                _, writer = await asyncio.open_connection(ip, port)
                writer.close()
                await writer.wait_closed()
                return {"ip": ip, "metadata": {"type": "mqtt_broker"}}
            except Exception:
                pass
        elif port == 8883:
            try:
                _, writer = await asyncio.open_connection(ip, port)
                writer.close()
                await writer.wait_closed()
                return {"ip": ip, "metadata": {"type": "mqtt_broker_tls"}}
            except Exception:
                pass
    except Exception:
        pass
    return {}


async def bulk_scan(
    ha_url: str, ha_token: str, subnet: str = DEFAULT_SUBNET
) -> list[dict]:
    """Scan for all known media devices on the network."""
    log.info(f"[discovery] bulk_scan starting: ha_url={ha_url}, ha_token_len={len(ha_token)}, subnet={subnet}")
    all_states = await ha_client.get_states(ha_url, ha_token)
    log.info(f"[discovery] bulk_scan got {len(all_states) if all_states else 0} states from HA")
    if not all_states:
        return []

    media_states = [s for s in all_states if s["entity_id"].startswith("media_player.")]

    # Step 1: Scan subnet once to build IP -> device map
    log.info(f"[discovery] Scanning subnet {subnet} for devices...")
    import ipaddress

    import aiohttp
    device_map = {}  # ip -> {type, metadata}

    all_ports = set()
    for ports in DEVICE_PORTS.values():
        for port, _ in ports:
            all_ports.add(port)
    port_list = sorted(all_ports)

    candidates = [
        str(ip) for ip in ipaddress.IPv4Network(subnet)
        if not str(ip).endswith(".0") and not str(ip).endswith(".255")
    ]

    batch_size = 30
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as client:
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            tasks = []
            task_map = []
            for ip in batch:
                for port in port_list:
                    tasks.append(_probe_port(client, ip, port))
                    task_map.append((ip, port))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for (ip, port), resp in zip(task_map, results, strict=False):
                if isinstance(resp, dict) and resp.get("ip") and ip not in device_map:
                    metadata = resp.get("metadata", {})
                    device_map[ip] = {
                        "type": metadata.get("type", "unknown"),
                        "metadata": metadata,
                    }

    log.info(f"[discovery] Found {len(device_map)} devices on network: {list(device_map.keys())}")

    # Step 2: Match entities to discovered devices
    semaphore = asyncio.Semaphore(5)

    async def _scan_one(state: dict):
        async with semaphore:
            entity_id = state["entity_id"]
            attrs = state.get("attributes", {})
            integration = attrs.get("integration", "")
            entity_lower = entity_id.lower()
            (attrs.get("friendly_name") or "").lower()

            # Try HA registry and entity attrs first
            result = await _discover_via_ha_registry(entity_id, ha_url, ha_token, None)
            if result:
                return result
            result = await _discover_via_entity_attrs(entity_id, ha_url, ha_token)
            if result:
                return result

            # Match to discovered device by type
            for ip, dev_info in device_map.items():
                dev_type = dev_info["type"]
                # Cast devices may have "chrome" or "cast" in entity_id
                type_aliases = {
                    "cast": ["cast", "chrome"],
                    "androidtv": ["android", "adb"],
                }
                match_terms = type_aliases.get(dev_type, [dev_type])
                if any(t in entity_lower for t in match_terms) or dev_type == integration.lower():
                    await device_registry.set_device(
                        entity_id,
                        ip=ip,
                        friendly_name=attrs.get("friendly_name", ""),
                        discovery_method="network_scan",
                        metadata=dev_info["metadata"],
                    )
                    log.info(f"[discovery] Matched {entity_id} to {ip} (type={dev_type})")
                    return await device_registry.get_device(entity_id)

            log.warning(f"[discovery] All strategies failed for {entity_id}")
            return None

    results = await asyncio.gather(*[_scan_one(s) for s in media_states], return_exceptions=True)
    return cast(list[dict], [r for r in results if r and not isinstance(r, Exception)])
