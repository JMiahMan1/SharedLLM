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
import logging
import os
import socket
from typing import Optional

import device_registry
import ha_client

log = logging.getLogger("execution.discovery")

DEFAULT_SUBNET = "192.168.2.0/24"

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

    result = await _discover_via_entity_attrs(entity_id, ha_url, ha_token)
    if result:
        return result

    result = await _discover_via_arp(entity_id, ha_url, ha_token)
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
    """Match entity to ARP table entries via hostname."""
    try:
        import subprocess
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if not state:
            return None

        friendly = state.get("attributes", {}).get("friendly_name", "").lower()
        entity_base = entity_id.split(".")[-1].lower().replace("_", " ")

        result = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return None

        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            hostname_part = parts[0].lower()
            ip_part = parts[1].strip("()")
            mac_part = parts[3] if len(parts) > 3 else ""

            if (friendly and any(word in hostname_part for word in friendly.split() if len(word) > 2)) or \
               (entity_base and any(word in hostname_part for word in entity_base.split() if len(word) > 2)):
                await device_registry.set_device(
                    entity_id,
                    ip=ip_part,
                    mac=mac_part,
                    hostname=hostname_part.rstrip("."),
                    friendly_name=state["attributes"].get("friendly_name", ""),
                    discovery_method="arp",
                )
                log.info(f"[discovery] Found {entity_id} via ARP: ip={ip_part} mac={mac_part}")
                return await device_registry.get_device(entity_id)
    except Exception as e:
        log.warning(f"[discovery] ARP lookup failed: {e}")
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

            if not friendly and not entity_base:
                return True

            searchable = f"{model} {serial} {device_name}".lower()
            for word in friendly.split():
                if len(word) > 2 and word in searchable:
                    log.info(f"[discovery] Name match for {entity_id}: '{word}' found in '{searchable}'")
                    return True
            for word in entity_base.split():
                if len(word) > 2 and word in searchable:
                    log.info(f"[discovery] Name match for {entity_id}: '{word}' found in '{searchable}'")
                    return True
            log.info(f"[discovery] Name mismatch for {entity_id}: friendly='{friendly}' entity_base='{entity_base}' searchable='{searchable}'")
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
                    return {"ip": ip, "metadata": {"serial": serial, "model": model}}
                except ET.ParseError:
                    return {"ip": ip}
        elif port == 3000:
            resp = await client.get(f"http://{ip}:3000", timeout=1)
            if resp.status_code == 200:
                return {"ip": ip}
        elif port == 8001:
            resp = await client.get(f"http://{ip}:8001/api/v2/", timeout=1)
            if resp.status_code == 200:
                try:
                    info = resp.json()
                    return {"ip": ip, "metadata": {"model": info.get("device", {}).get("modelName", "")}}
                except Exception:
                    return {"ip": ip}
        elif port == 8009:
            resp = await client.get(f"http://{ip}:8009/setup/eureka_info", timeout=1)
            if resp.status_code == 200:
                return {"ip": ip}
        elif port == 5555:
            try:
                _, writer = await asyncio.open_connection(ip, port)
                writer.close()
                await writer.wait_closed()
                return {"ip": ip}
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

    # Limit concurrency to avoid overwhelming HA with simultaneous get_state calls
    semaphore = asyncio.Semaphore(5)

    async def _scan_one(state: dict):
        async with semaphore:
            entity_id = state["entity_id"]
            attrs = state.get("attributes", {})
            integration = attrs.get("integration", "")
            device_type = None
            if "roku" in entity_id.lower() or integration == "roku":
                device_type = "roku"
            elif "webos" in entity_id.lower() or integration == "webostv":
                device_type = "webos"
            elif "samsung" in entity_id.lower() or integration == "samsungtv":
                device_type = "samsung"
            elif "android" in entity_id.lower() or integration == "androidtv":
                device_type = "androidtv"
            elif "cast" in entity_id.lower() or "chrome" in entity_id.lower() or integration == "cast":
                device_type = "cast"
            elif "esphome" in entity_id.lower() or integration == "esphome":
                device_type = "esphome"
            elif "tasmota" in entity_id.lower() or integration == "tasmota":
                device_type = "tasmota"
            elif "mqtt" in entity_id.lower() or integration == "mqtt":
                device_type = "mqtt"
            return await discover_device(
                entity_id, ha_url, ha_token, device_type, subnet, use_cache=False
            )

    results = await asyncio.gather(*[_scan_one(s) for s in media_states], return_exceptions=True)
    return [r for r in results if r and not isinstance(r, Exception)]
