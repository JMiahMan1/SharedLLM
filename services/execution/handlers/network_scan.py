# services/execution/handlers/network_scan.py
"""
Comprehensive network device scanner for smart TVs and media devices.

Scans the local subnet for Roku, webOS, Samsung, Sony Bravia, Chromecast,
and Android TV devices. Enriches results with MAC addresses from ARP cache
and detailed device information from vendor-specific APIs.
"""
import asyncio
import ipaddress
import logging
import socket

import aiohttp

log = logging.getLogger("execution.network_scan")

# Auto-detect subnet from host network interfaces
def get_local_subnet() -> str:
    """Detect the local subnet from the host's network interfaces."""
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


# Device type port definitions
DEVICE_PORTS = {
    "roku": [8060],
    "webos": [3000, 3001, 9080],
    "samsung": [8001, 8002, 9197],
    "sony_bravia": [80, 52323],
    "chromecast": [8009],
    "androidtv": [5555],
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
        List of discovered devices with IP, type, and metadata.
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

    # Generate IP list
    try:
        network = ipaddress.IPv4Network(subnet, strict=False)
        ips = [
            str(ip) for ip in network
            if not str(ip).endswith(".0") and not str(ip).endswith(".255")
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
    """Fast concurrent port scan returning (ip, port) pairs."""
    async def _check(ip: str, port: int) -> tuple[str, int] | None:
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

    seen_ips = set()
    devices = []

    async with aiohttp.ClientSession(connector=connector, timeout=aio_timeout) as client:
        for ip, port in responsive:
            if ip in seen_ips:
                continue
            seen_ips.add(ip)

            device = None

            # Roku (port 8060)
            if port == 8060:
                device = await _probe_roku(client, ip, timeout)

            # webOS (port 3000/3001/9080)
            elif port in (3000, 3001, 9080):
                device = await _probe_webos(client, ip, timeout)

            # Samsung (port 8001/8002)
            elif port in (8001, 8002):
                device = await _probe_samsung(client, ip, timeout)

            # Sony Bravia (port 80)
            elif port == 80:
                device = await _probe_sony_bravia(client, ip, timeout)

            # Chromecast (port 8009)
            elif port == 8009:
                device = await _probe_chromecast(ip, timeout)

            # DLNA (port 9197)
            elif port == 9197:
                device = await _probe_dlna(client, ip, timeout)

            # ESPHome (port 6053)
            elif port == 6053:
                device = await _probe_esphome(ip, timeout)

            if device:
                # Add MAC from ARP cache
                if arp_cache and ip in arp_cache:
                    device["mac"] = arp_cache[ip]
                devices.append(device)

    return devices


async def _probe_roku(client: aiohttp.ClientSession, ip: str, timeout: float) -> dict | None:
    """Probe Roku device via ECP API."""
    try:
        async with client.get(f"http://{ip}:8060/query/device-info", timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status != 200:
                return None

            import xml.etree.ElementTree as ET
            root = ET.fromstring(await resp.read())

            return {
                "ip": ip,
                "type": "roku",
                "friendly_name": root.findtext("user-device-name", "") or root.findtext("friendly-device-name", ""),
                "model": root.findtext("model-name", ""),
                "model_number": root.findtext("model-number", ""),
                "serial": root.findtext("serial-number", ""),
                "software_version": root.findtext("software-version", ""),
                "power_mode": root.findtext("power-mode", ""),
                "screen_size": root.findtext("screen-size", ""),
                "is_tv": root.findtext("is-tv", "false") == "true",
                "supports_ethernet": root.findtext("supports-ethernet", "false") == "true",
                "wifi_mac": root.findtext("wifi-mac", ""),
                "location": root.findtext("user-device-location", ""),
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
                            return {
                                "ip": ip,
                                "type": "webos",
                                "model": data.get("modelName", ""),
                                "software_version": data.get("softwareVersion", ""),
                                "device_id": data.get("deviceId", ""),
                            }
                except Exception:
                    pass

            # Fallback: just return basic info
            return {
                "ip": ip,
                "type": "webos",
                "friendly_name": "LG webOS TV",
            }
    except Exception as e:
        log.debug(f"[network_scan] webOS probe failed for {ip}: {e}")
    return None


async def _probe_samsung(client: aiohttp.ClientSession, ip: str, timeout: float) -> dict | None:
    """Probe Samsung TV via REST API."""
    for port in (8001, 8002):
        try:
            scheme = "https" if port == 8002 else "http"
            async with client.get(f"{scheme}://{ip}:{port}/api/v2/", timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    device = data.get("device", {})
                    return {
                        "ip": ip,
                        "type": "samsung",
                        "friendly_name": data.get("name", ""),
                        "model": device.get("modelName", ""),
                        "serial": device.get("serialNumber", ""),
                        "software_version": data.get("softwareVersion", ""),
                        "os_type": device.get("OS", ""),
                        "udn": device.get("udn", ""),
                        "wifi_mac": device.get("wifiMac", ""),
                        "supports_wol": device.get("supportWOL", False),
                    }
        except Exception:
            continue
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
