# services/execution/handlers/network_scan.py
"""
Network device scanner for smart TVs and media devices.

Scans the local subnet for Roku, webOS, Samsung, Sony Bravia, Chromecast,
and Android TV devices. Enriches results with MAC addresses from ARP cache
and detailed device information from vendor-specific APIs.
"""
import asyncio
import ipaddress
import logging
import os
import socket

import aiohttp

log = logging.getLogger("execution.network_scan")

# Auto-detect subnet from host network interfaces
def get_local_subnet() -> str:
    """Detect the local subnet from the host's network interfaces."""
    env_subnet = os.environ.get("SCAN_SUBNET") or os.environ.get("LOCAL_SUBNET")
    if env_subnet:
        return env_subnet
    try:
        # Try to get the default route interface
        with open("/proc/net/route") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 3 and parts[1] == "00000000":
                    iface = parts[0]
                    # Get IP and mask for this interface
                    with open(f"/sys/class/net/{iface}/address"):
                        pass  # MAC address, not needed
                    # Parse IP from ifconfig or ip command
                    result = subprocess_run(["ip", "-j", "addr", "show", iface])
                    if result:
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


def _get_self_ips() -> set[str]:
    ips = {"127.0.0.1", "0.0.0.0"}
    ext = (os.environ.get("EXECUTION_EXTERNAL_HOST") or "").strip()
    if ext:
        ips.add(ext)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return ips


def subprocess_run(args, timeout=5):
    """Run a subprocess and return stdout."""
    import subprocess
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def get_arp_cache() -> dict:
    """Read the ARP cache and return IP -> MAC mapping."""
    arp_map = {}
    try:
        with open("/proc/net/arp") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 4 and parts[0] != "IP":
                    arp_map[parts[0]] = parts[3]
    except Exception:
        pass
    return arp_map


# Device type port definitions across TV generations
DEVICE_PORTS = {
    "roku": [8060],
    "webos": [3000, 3001, 9080],
    "samsung": [8001, 8002, 55000, 9197],
    "sony_bravia": [80, 52323],
    "chromecast": [8008, 8009],
    "androidtv": [6466, 5555, 8008, 8009],
    "dlna": [9197, 8200],
    "esphome": [6053],
}

ALL_TV_PORTS = sorted(set(
    port for ports in DEVICE_PORTS.values() for port in ports
))


async def scan_network(
    subnet: str | None = None,
    device_type: str | None = None,
    include_mac: bool = True,
    timeout: float = 2.0,
) -> list[dict]:
    """
    Scan the local network for smart TVs and media devices.

    Args:
        subnet: Subnet to scan (e.g. "192.168.2.0/24"). Auto-detected if None.
        device_type: Filter by device type. None = scan all.
        include_mac: Include MAC address from ARP cache.
        timeout: Connection timeout per port.

    Returns:
        List of discovered devices with IP, type, OS version, communication methods, and metadata.
    """
    if not subnet:
        subnet = get_local_subnet()
    log.info(f"[network_scan] Scanning {subnet} for devices (type={device_type or 'all'})")

    # Get ARP cache for MAC enrichment
    arp_cache = get_arp_cache() if include_mac else {}

    # Determine which ports to scan
    if device_type and device_type in DEVICE_PORTS:
        ports = DEVICE_PORTS[device_type]
    elif device_type == "all":
        ports = ALL_TV_PORTS
    else:
        ports = ALL_TV_PORTS

    self_ips = _get_self_ips()

    # Generate IP list
    try:
        network = ipaddress.IPv4Network(subnet, strict=False)
        ips = [
            str(ip) for ip in network
            if not str(ip).endswith(".0") and not str(ip).endswith(".255") and str(ip) not in self_ips
        ]
    except Exception as e:
        log.error(f"[network_scan] Invalid subnet {subnet}: {e}")
        return []

    # Phase 1: Fast port scan to find responsive IPs
    log.info(f"[network_scan] Phase 1: Scanning {len(ips)} IPs on {len(ports)} ports")
    responsive = await _fast_port_scan(ips, ports, timeout)
    log.info(f"[network_scan] Found {len(responsive)} responsive IP:port pairs")

    if not responsive:
        return []

    # Phase 2: Enrich with device-specific APIs
    devices = await _enrich_devices(responsive, arp_cache, timeout)

    # Filter by device type if specified
    if device_type and device_type != "all":
        devices = [d for d in devices if d.get("type") == device_type]

    log.info(f"[network_scan] Discovered {len(devices)} devices")
    return devices


async def _fast_port_scan(
    ips: list[str],
    ports: list[int],
    timeout: float,
) -> list[tuple[str, int]]:
    """Fast concurrent port scan returning (ip, port) pairs with bounded file descriptors."""
    sem = asyncio.Semaphore(48)

    async def _check(ip: str, port: int) -> tuple[str, int] | None:
        async with sem:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, port),
                    timeout=timeout,
                )
                writer.close()
                await writer.wait_closed()
                return (ip, port)
            except Exception:
                return None

    tasks = []
    for ip in ips:
        for port in ports:
            tasks.append(_check(ip, port))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    filtered: list[tuple[str, int]] = [r for r in results if r is not None and not isinstance(r, Exception)]  # type: ignore[arg-type]
    return filtered


async def _enrich_devices(
    responsive: list[tuple[str, int]],
    arp_cache: dict,
    timeout: float,
) -> list[dict]:
    """Enrich responsive IPs with device-specific API data."""
    import ssl
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    aio_timeout = aiohttp.ClientTimeout(total=timeout)

    ports_by_ip: dict[str, set[int]] = {}
    for ip, port in responsive:
        ports_by_ip.setdefault(ip, set()).add(port)

    devices = []

    async with aiohttp.ClientSession(connector=connector, timeout=aio_timeout) as client:
        for ip, open_ports in ports_by_ip.items():
            device = None

            # Roku (port 8060)
            if 8060 in open_ports:
                device = await _probe_roku(client, ip, timeout)

            # Android TV / Chromecast (ports 6466, 5555, 8008, 8009)
            if not device and (open_ports & {6466, 5555, 8008, 8009}):
                device = await _probe_android_or_cast(client, ip, open_ports, timeout)

            # Samsung (port 8001/8002/55000)
            if not device and (open_ports & {8001, 8002, 55000}):
                device = await _probe_samsung(client, ip, timeout, open_ports=open_ports)

            # webOS (port 3000/3001/9080)
            if not device and (open_ports & {3000, 3001, 9080}):
                device = await _probe_webos(client, ip, timeout)

            # Sony Bravia (port 80/52323)
            if not device and (open_ports & {80, 52323}):
                device = await _probe_sony_bravia(client, ip, timeout)

            # DLNA (port 9197)
            if not device and 9197 in open_ports:
                device = await _probe_dlna(client, ip, timeout)

            # ESPHome (port 6053)
            if not device and 6053 in open_ports:
                device = await _probe_esphome(ip, timeout)

            if device:
                device["open_ports"] = sorted(open_ports)
                # Add MAC from ARP cache if not already present
                if arp_cache and ip in arp_cache and not device.get("mac"):
                    device["mac"] = arp_cache[ip]
                devices.append(device)

    return devices


async def _probe_roku(client: aiohttp.ClientSession, ip: str, timeout: float) -> dict | None:
    """Probe Roku device via ECP API and inspect installed channels (Media Assistant 782875)."""
    try:
        async with client.get(f"http://{ip}:8060/query/device-info", timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status != 200:
                return None

            import xml.etree.ElementTree as ET
            root = ET.fromstring(await resp.read())

            sw_ver = root.findtext("software-version", "")
            sw_build = root.findtext("software-build", "")
            has_media_assistant = False
            has_roku_media_player = False
            try:
                async with client.get(f"http://{ip}:8060/query/apps", timeout=aiohttp.ClientTimeout(total=1.2)) as apps_resp:
                    if apps_resp.status == 200:
                        apps_root = ET.fromstring(await apps_resp.read())
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
                "type": "roku",
                "friendly_name": root.findtext("user-device-name", "") or root.findtext("friendly-device-name", ""),
                "model": root.findtext("model-name", ""),
                "model_number": root.findtext("model-number", ""),
                "vendor": root.findtext("vendor-name", ""),
                "serial": root.findtext("serial-number", ""),
                "software_version": sw_ver,
                "os_version": f"Roku OS {sw_ver} (build {sw_build})".strip() if sw_ver else "Roku OS",
                "power_mode": root.findtext("power-mode", ""),
                "screen_size": root.findtext("screen-size", ""),
                "is_tv": root.findtext("is-tv", "false") == "true",
                "supports_ethernet": root.findtext("supports-ethernet", "false") == "true",
                "supports_ecs_microphone": root.findtext("supports-ecs-microphone", "false") == "true",
                "wifi_mac": root.findtext("wifi-mac", ""),
                "mac": root.findtext("wifi-mac", "") or root.findtext("ethernet-mac", ""),
                "location": root.findtext("user-device-location", ""),
                "has_media_assistant": has_media_assistant,
                "has_roku_media_player": has_roku_media_player,
                "communication_methods": comm_methods,
            }
    except Exception as e:
        log.debug(f"[network_scan] Roku probe failed for {ip}: {e}")
        return None


async def _probe_webos(client: aiohttp.ClientSession, ip: str, timeout: float) -> dict | None:
    """Probe webOS TV via status endpoint and WebSocket."""
    try:
        # Try port 9080 first (Netflix chip status)
        async with client.get(f"http://{ip}:9080/", timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status == 200 and "status=ok" in await resp.text():
                # Try to get more info via WebSocket on port 3000
                try:
                    import websockets
                    uri = f"ws://{ip}:3000/"
                    async with websockets.connect(uri, ping_interval=None, close_timeout=2) as ws:
                        msg = '{"id":"1","type":"request","uri":"ssap://system/getSystemInfo"}'
                        await ws.send(msg)
                        resp_ws = await asyncio.wait_for(ws.recv(), timeout=3)
                        import json
                        data = json.loads(resp_ws)
                        if data.get("returnValue"):
                            sw_ver = data.get("softwareVersion", "")
                            return {
                                "ip": ip,
                                "type": "webos",
                                "model": data.get("modelName", ""),
                                "software_version": sw_ver,
                                "os_version": f"LG webOS {sw_ver}".strip(),
                                "device_id": data.get("deviceId", ""),
                                "communication_methods": ["ha_notify_toast", "webostv_command_toast", "ha_play_media", "music_assistant"],
                            }
                except Exception:
                    pass

                return {
                    "ip": ip,
                    "type": "webos",
                    "friendly_name": "LG webOS TV",
                    "os_version": "LG webOS",
                    "communication_methods": ["ha_notify_toast", "webostv_command_toast", "ha_play_media", "music_assistant"],
                }
    except Exception as e:
        log.debug(f"[network_scan] webOS probe failed for {ip}: {e}")
    return None


async def _probe_samsung(
    client: aiohttp.ClientSession,
    ip: str,
    timeout: float,
    open_ports: set[int] | None = None,
) -> dict | None:
    """Probe Samsung TV via Tizen REST API (8001/8002) or legacy Orsay (55000)."""
    for port in (8001, 8002):
        try:
            scheme = "https" if port == 8002 else "http"
            async with client.get(f"{scheme}://{ip}:{port}/api/v2/", timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    device = data.get("device", {})
                    if not isinstance(device, dict) or not (device.get("modelName") or device.get("OS")):
                        continue
                    os_type = device.get("OS", "Tizen")
                    sw_ver = data.get("softwareVersion") or data.get("version") or ""
                    return {
                        "ip": ip,
                        "type": "samsung",
                        "generation": "tizen",
                        "friendly_name": data.get("name", ""),
                        "model": device.get("modelName", ""),
                        "serial": device.get("serialNumber", ""),
                        "software_version": sw_ver,
                        "os_type": os_type,
                        "os_version": f"Samsung {os_type} {sw_ver}".strip(),
                        "udn": device.get("udn", ""),
                        "wifi_mac": device.get("wifiMac", ""),
                        "mac": device.get("wifiMac", ""),
                        "supports_wol": device.get("supportWOL", False),
                        "voice_support": device.get("VoiceSupport") == "true",
                        "communication_methods": ["music_assistant", "dlna_dmr", "ha_play_media", "samsungtv_websocket"],
                    }
        except Exception:
            continue
    if open_ports and 55000 in open_ports:
        return {
            "ip": ip,
            "type": "samsung",
            "generation": "legacy_orsay",
            "friendly_name": "Samsung Legacy TV",
            "os_type": "Orsay",
            "os_version": "Samsung Orsay (Legacy)",
            "communication_methods": ["ha_samsungtv_legacy", "dlna_dmr", "room_cast_companion"],
        }
    return None


async def _probe_android_or_cast(
    client: aiohttp.ClientSession,
    ip: str,
    open_ports: set[int],
    timeout: float,
) -> dict | None:
    """Probe Android TV / Chromecast via port 8008 eureka_info and remote ports 6466/5555."""
    has_androidtv_remote = 6466 in open_ports
    has_adb = 5555 in open_ports
    is_android_tv = has_androidtv_remote or has_adb
    try:
        async with client.get(
            f"http://{ip}:8008/setup/eureka_info?params=version,name,build_info,device_info,net,wifi,audio",
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, dict) and (data.get("cast_build_revision") or data.get("ssdp_udn")):
                    cast_rev = data.get("cast_build_revision", "")
                    comm = ["cast_sibling", "music_assistant", "ha_play_media"]
                    if has_androidtv_remote:
                        comm.insert(0, "androidtv_remote")
                    if has_adb:
                        comm.append("adb")
                    return {
                        "ip": ip,
                        "type": "androidtv" if is_android_tv else "chromecast",
                        "friendly_name": data.get("name", ""),
                        "software_version": cast_rev,
                        "os_version": f"Android TV (Cast {cast_rev})" if is_android_tv else f"Google Cast {cast_rev}".strip(),
                        "mac": data.get("mac_address", ""),
                        "uuid": data.get("ssdp_udn", ""),
                        "has_androidtv_remote": has_androidtv_remote,
                        "has_adb": has_adb,
                        "communication_methods": comm,
                    }
    except Exception:
        pass
    if is_android_tv:
        return {
            "ip": ip,
            "type": "androidtv",
            "friendly_name": "Android TV",
            "os_version": "Android TV",
            "has_androidtv_remote": has_androidtv_remote,
            "has_adb": has_adb,
            "communication_methods": ["androidtv_remote", "cast_sibling", "music_assistant", "ha_play_media"],
        }
    if 8009 in open_ports:
        return await _probe_chromecast(ip, timeout)
    return None


async def _probe_sony_bravia(client: aiohttp.ClientSession, ip: str, timeout: float) -> dict | None:
    """Probe Sony Bravia TV via UPnP/IRCC API."""
    try:
        # Try the Sony ScalarWeb API descriptor
        async with client.get(f"http://{ip}:8008/ssdp/device-desc.xml", timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status == 200 and "Sony" in await resp.text():
                import xml.etree.ElementTree as ET
                root = ET.fromstring(await resp.read())
                # Parse namespaces
                ns = {"root": "urn:schemas-upnp-org:device-1-0"}
                model = root.find(".//root:modelName", ns)
                return {
                    "ip": ip,
                    "type": "sony_bravia",
                    "friendly_name": root.findtext(".//root:friendlyName", "", ns),
                    "model": model.text if model is not None else "",
                }
    except Exception:
        pass

    # Try port 52323 (DLNA/IRCC)
    try:
        async with client.get(f"http://{ip}:52323/dmr.xml", timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status == 200:
                return {
                    "ip": ip,
                    "type": "sony_bravia",
                    "friendly_name": "Sony Bravia TV",
                }
    except Exception:
        pass

    return None


async def _probe_chromecast(ip: str, timeout: float) -> dict | None:
    """Probe Chromecast device."""
    try:
        from pychromecast import get_chromecasts  # pyright: ignore[reportMissingImports]
        casts = await asyncio.wait_for(
            asyncio.to_thread(get_chromecasts, timeout=timeout),
            timeout=timeout + 2,
        )
        for cast in casts:
            if cast.host == ip:
                return {
                    "ip": ip,
                    "type": "chromecast",
                    "friendly_name": cast.name,
                    "model": cast.model_name,
                    "manufacturer": cast.manufacturer,
                    "uuid": str(cast.uuid),
                }
    except Exception as e:
        log.debug(f"[network_scan] Chromecast probe failed for {ip}: {e}")
    return None


async def _probe_dlna(client: aiohttp.ClientSession, ip: str, timeout: float) -> dict | None:
    """Probe DLNA device."""
    try:
        async with client.get(f"http://{ip}:9197/dmr", timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status == 200:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(await resp.read())
                ns = {"root": "urn:schemas-upnp-org:device-1-0"}
                return {
                    "ip": ip,
                    "type": "dlna",
                    "friendly_name": root.findtext(".//root:friendlyName", "", ns),
                    "model": root.findtext(".//root:modelName", "", ns),
                "manufacturer": root.findtext(".//root:manufacturer", "", ns),
            }
    except Exception:
        pass
    return None


async def _probe_esphome(ip: str, timeout: float) -> dict | None:
    """Probe ESPHome device via native API."""
    try:
        import aioesphomeapi  # pyright: ignore[reportMissingImports]
        client = aioesphomeapi.APIClient(ip, 6053, "")
        await asyncio.wait_for(client.connect(login=True), timeout=timeout)
        try:
            device_info = await client.device_info()
            entities, services = await client.list_entities_services()

            entity_types = set()
            for entity in entities:
                if hasattr(entity, "type_"):
                    entity_types.add(entity.type_)
                elif hasattr(entity, "__class__"):
                    entity_types.add(entity.__class__.__name__)

            return {
                "ip": ip,
                "type": "esphome",
                "friendly_name": getattr(device_info, "name", "") or getattr(device_info, "friendly_name", ""),
                "model": getattr(device_info, "model", ""),
                "manufacturer": getattr(device_info, "manufacturer", "ESPHome"),
                "software_version": getattr(device_info, "esphome_version", ""),
                "platform": getattr(device_info, "compile_platform", ""),
                "board": getattr(device_info, "board", ""),
                "mac_address": getattr(device_info, "mac_address", ""),
                "entity_count": len(entities),
                "entity_types": sorted(entity_types),
                "service_count": len(services),
                "encryption_required": False,
            }
        finally:
            await client.disconnect()
    except Exception as e:
        error_type = type(e).__name__
        if "RequiresEncryption" in error_type or "encryption" in str(e).lower():
            return {
                "ip": ip,
                "type": "esphome",
                "friendly_name": "",
                "model": "",
                "manufacturer": "ESPHome",
                "software_version": "",
                "encryption_required": True,
                "note": "Device requires noise encryption key",
            }
        log.debug(f"[network_scan] ESPHome probe failed for {ip}: {e}")
    return None
