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
import ipaddress
import logging
import os
import socket
from typing import Optional

import device_registry
import ha_client

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
        return "192.168.1.0/24"


DEFAULT_SUBNET = get_local_subnet()

DEVICE_PORTS = {
    "roku": [(8060, "roku:ecp")],
    "webos": [(3000, "webos"), (7676, "webos")],
    "samsung": [(8001, "samsung"), (8002, "samsung")],
    "androidtv": [(5555, "adb")],
    "cast": [(8009, "cast")],
    "dlna": [(9197, "dlna"), (8200, "dlna")],
    "esphome": [(80, "esphome"), (8080, "espcam")],
    "tasmota": [(80, "tasmota")],
    "mqtt": [(1883, "mqtt"), (8883, "mqtt-tls")],
}


async def discover_device(
    entity_id: str,
    ha_url: str,
    ha_token: str,
    device_type: Optional[str] = None,
    subnet: str = DEFAULT_SUBNET,
    use_cache: bool = True,
) -> Optional[dict]:
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
    entity_id: str, ha_url: str, ha_token: str, device_type: Optional[str] = None
) -> Optional[dict]:
    """Look up IP/MAC via HA device registry REST API."""
    import httpx
    try:
        headers = {"Authorization": f"Bearer {ha_token}"}
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if not state:
            return None

        attrs = state.get("attributes", {})
        friendly_name = attrs.get("friendly_name", "")

        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            resp = await client.get(f"{ha_url}/api/config/config_entries/entry", headers=headers)
            if resp.status_code != 200:
                return None

            entries = resp.json()
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
                        if dev_resp.status_code == 200:
                            device_registry_list = dev_resp.json()
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
                try:
                    hostname = socket.gethostbyaddr(host)[0]
                except Exception:
                    pass

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
            import httpx
            headers = {"Authorization": f"Bearer {ha_token}"}
            state = await ha_client.get_state(ha_url, ha_token, entity_id)
            if not state:
                return None
            friendly_name = state.get("attributes", {}).get("friendly_name", "")
            async with httpx.AsyncClient(verify=False, timeout=10) as client:
                resp = await client.get(f"{ha_url}/api/config/config_entries/entry", headers=headers)
                if resp.status_code == 200:
                    for entry in resp.json():
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
) -> Optional[dict]:
    """Get IP from HomeKit controller diagnostics for WebOS TVs.

    WebOS config entries have host/IP redacted in REST API, but HomeKit
    controller diagnostics expose AccessoryIPs. This bridges the gap by
    matching webostv devices to their HomeKit sibling entries.
    """
    import httpx
    try:
        headers = {"Authorization": f"Bearer {ha_token}"}
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if not state:
            return None

        friendly_name = state.get("attributes", {}).get("friendly_name", "")
        entity_lower = entity_id.lower()
        friendly_lower = friendly_name.lower()

        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            # Get device registry to find webostv device info
            dev_resp = await client.get(f"{ha_url}/api/config/device_registry/list", headers=headers)
            if dev_resp.status_code != 200:
                return None
            device_registry_list = dev_resp.json()

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
            if entries_resp.status_code != 200:
                return None

            for entry in entries_resp.json():
                if entry.get("domain") != "homekit_controller":
                    continue

                entry_id = entry.get("entry_id")
                if not entry_id:
                    continue

                # Call diagnostics endpoint
                diag_resp = await client.get(f"{ha_url}/api/diagnostics/config_entry/{entry_id}", headers=headers)
                if diag_resp.status_code != 200:
                    continue

                diag_data = diag_resp.json()
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
) -> Optional[dict]:
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
) -> Optional[dict]:
    """Get IPs from ARP table, probe for device ports, match by device info."""
    try:
        import subprocess
        import httpx
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if not state:
            return None

        friendly = state.get("attributes", {}).get("friendly_name", "").lower()
        entity_base = entity_id.split(".")[-1].lower().replace("_", " ")

        # Get IPs from ARP table
        arp_ips = []
        try:
            with open("/proc/net/arp", "r") as f:
                lines = f.read().splitlines()[1:]  # skip header
            for line in lines:
                parts = line.split()
                if len(parts) >= 4:
                    ip_part = parts[0]
                    mac_part = parts[3]
                    if mac_part != "00:00:00:00:00:00" and mac_part != "incomplete":
                        arp_ips.append((ip_part, mac_part))
        except FileNotFoundError:
            result = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=5)
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
        async with httpx.AsyncClient(verify=False, timeout=2) as client:
            for ip, mac in arp_ips:
                for port in webos_ports:
                    try:
                        resp = await client.get(f"http://{ip}:{port}", timeout=1)
                        if resp.status_code == 200:
                            # Check if device info matches entity
                            body = resp.text.lower()
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
) -> Optional[dict]:
    """Scan subnets with arp-scan, probe for device ports, match by device info."""
    try:
        import subprocess
        import httpx
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
            adjacent = list(net.network_address)
            adjacent[2] = (adjacent[2] + 1) % 256
            subnets.append(f"{adjacent[0]}.{adjacent[1]}.{adjacent[2]}.0/24")
        except Exception:
            subnets = [local_subnet]
        found_devices = []

        for subnet in subnets:
            try:
                result = subprocess.run(
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
        async with httpx.AsyncClient(verify=False, timeout=2) as client:
            for ip, mac, vendor in found_devices:
                for port in webos_ports:
                    try:
                        resp = await client.get(f"http://{ip}:{port}", timeout=1)
                        if resp.status_code == 200:
                            body = resp.text.lower()
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
    entity_id: str, ha_url: str, ha_token: str, router_ip: str = "192.168.2.1", community: str = "public"
) -> Optional[dict]:
    """Get MAC/IP from router ARP table via SNMP walk."""
    try:
        import subprocess
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if not state:
            return None

        friendly = state.get("attributes", {}).get("friendly_name", "").lower()
        entity_base = entity_id.split(".")[-1].lower().replace("_", " ")

        # SNMP OID: ipNetToMediaPhysAddress (1.3.6.1.2.1.4.22.1.2)
        # Returns: ifIndex.IP.OCTET = MAC address
        result = subprocess.run(
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
            try:
                hostname = socket.gethostbyaddr(ip)[0].lower()
            except Exception:
                pass
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
) -> Optional[dict]:
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
                    hostname=hostname.rstrip(".local"),
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
    entity_id: str, device_type: Optional[str] = None
) -> Optional[dict]:
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
                    except sock_module.timeout:
                        break
                sock.close()
            except Exception:
                continue
    except Exception as e:
        log.warning(f"[discovery] SSDP failed: {e}")
    return None


async def _discover_via_network_scan(
    entity_id: str, ha_url: str, ha_token: str, device_type: Optional[str] = None, subnet: str = DEFAULT_SUBNET
) -> Optional[dict]:
    """Scan subnet for device by probing known ports.

    Only returns a match if the probed device info correlates with the entity
    (friendly name, model, or serial match). Prevents all entities mapping to
    the first responding IP on the subnet.
    """
    import httpx
    import ipaddress

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

            if device_type and device_type in entity_id.lower():
                return True

            return False

        async with httpx.AsyncClient(verify=False) as client:
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
                for (ip, port), resp in zip(zip(task_map, [p for _, p in ports] * len(batch)), results):
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
    """Probe a single IP:port and return device info if found."""
    try:
        if port == 8060:
            resp = await client.get(f"http://{ip}:8060/query/device-info", timeout=1)
            if resp.status_code == 200 and b"roku" in resp.content.lower():
                import xml.etree.ElementTree as ET
                try:
                    root = ET.fromstring(resp.content)
                    serial = root.findtext("serial-number", "")
                    model = root.findtext("model-name", "")
                    return {"ip": ip, "metadata": {"serial": serial, "model": model, "type": "roku"}}
                except ET.ParseError:
                    return {"ip": ip, "metadata": {"type": "roku"}}
        elif port == 3000:
            resp = await client.get(f"http://{ip}:3000", timeout=1)
            if resp.status_code == 200:
                return {"ip": ip, "metadata": {"type": "webos"}}
        elif port == 8001:
            resp = await client.get(f"http://{ip}:8001/api/v2/", timeout=1)
            if resp.status_code == 200:
                try:
                    info = resp.json()
                    return {"ip": ip, "metadata": {"model": info.get("device", {}).get("modelName", ""), "type": "samsung"}}
                except Exception:
                    return {"ip": ip, "metadata": {"type": "samsung"}}
        elif port == 8009:
            try:
                _, writer = await asyncio.open_connection(ip, port)
                writer.close()
                await writer.wait_closed()
                return {"ip": ip, "metadata": {"type": "cast"}}
            except Exception:
                pass
        elif port == 5555:
            try:
                _, writer = await asyncio.open_connection(ip, port)
                writer.close()
                await writer.wait_closed()
                return {"ip": ip, "metadata": {"type": "androidtv"}}
            except Exception:
                pass
        elif port == 80:
            resp = await client.get(f"http://{ip}/", timeout=1)
            if resp.status_code == 200:
                content = resp.text.lower()
                import re
                name_match = re.search(r"<title>(.*?)</title>", resp.text, re.IGNORECASE)
                device_name = name_match.group(1) if name_match else ""
                if "tasmota" in content:
                    return {"ip": ip, "metadata": {"device_name": device_name, "type": "tasmota"}}
                if "esphome" in content or "esp" in content:
                    return {"ip": ip, "metadata": {"device_name": device_name, "type": "esphome"}}
        elif port == 8080:
            resp = await client.get(f"http://{ip}:8080/", timeout=1)
            if resp.status_code == 200:
                content = resp.text.lower()
                if "esp" in content or "cam" in content or "stream" in content:
                    return {"ip": ip, "metadata": {"type": "espcam"}}
            try:
                stream_resp = await client.get(f"http://{ip}:8080/stream", timeout=1)
                if stream_resp.status_code == 200:
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
    import httpx
    import ipaddress
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
    async with httpx.AsyncClient(verify=False) as client:
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            tasks = []
            task_map = []
            for ip in batch:
                for port in port_list:
                    tasks.append(_probe_port(client, ip, port))
                    task_map.append((ip, port))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for (ip, port), resp in zip(task_map, results):
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
    return [r for r in results if r and not isinstance(r, Exception)]
