"""
Shared Network Device Discovery System
Supports SSDP-based discovery for smart home devices (Roku, Android TV, WebOS, etc.)
"""
import socket
import logging
import requests
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass
from abc import ABC, abstractmethod
from app.settings import run_blocking

log = logging.getLogger(__name__)

# SSDP constants
SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SSDP_TIMEOUT = 3


@dataclass
class DiscoveredDevice:
    """Represents a discovered network device"""
    ip: str
    device_type: str  # 'roku', 'androidtv', 'webos', etc.
    serial_number: Optional[str] = None
    model: Optional[str] = None
    manufacturer: Optional[str] = None
    friendly_name: Optional[str] = None
    raw_info: Dict = None


class DeviceDiscoveryProtocol(ABC):
    """Base class for device-specific discovery protocols"""
    
    @property
    @abstractmethod
    def ssdp_search_target(self) -> str:
        """SSDP ST (Search Target) for this device type"""
        pass
    
    @property
    @abstractmethod
    def device_type(self) -> str:
        """Device type identifier"""
        pass
    
    @abstractmethod
    def get_device_info(self, ip: str) -> Optional[DiscoveredDevice]:
        """Fetch detailed device info from IP address"""
        pass


class RokuDiscoveryProtocol(DeviceDiscoveryProtocol):
    """Roku device discovery via port scan and SSDP"""
    
    @property
    def ssdp_search_target(self) -> str:
        return "roku:ecp"
    
    @property
    def device_type(self) -> str:
        return "roku"
    
    async def scan_network_for_roku(self, subnet: str = "192.168.2.0/24", timeout: float = 2.0) -> List[str]:
        """
        Scan network for devices listening on Roku ECP port 8060
        Returns list of IP addresses that respond on port 8060
        """
        import asyncio
        import ipaddress
        
        async def check_port(ip: str, port: int = 8060, timeout: float = timeout) -> Optional[str]:
            """Check if port is open on given IP"""
            try:
                conn = asyncio.open_connection(ip, port)
                reader, writer = await asyncio.wait_for(conn, timeout=timeout)
                writer.close()
                await writer.wait_closed()
                return ip
            except:
                return None
        
        try:
            # Use ipaddress module to get hosts properly
            network = ipaddress.ip_network(subnet, strict=False)
            
            # Limit concurrency to chunks to avoid too many open files/sockets
            all_hosts = list(network.hosts())
            found_ips = []
            chunk_size = 50
            
            for i in range(0, len(all_hosts), chunk_size):
                chunk = all_hosts[i:i + chunk_size]
                tasks = [check_port(str(ip)) for ip in chunk]
                results = await asyncio.gather(*tasks)
                found_ips.extend([ip for ip in results if ip is not None])
            
            log.info(f"[Discovery] Port scan found {len(found_ips)} devices on port 8060")
            return found_ips
        except Exception as e:
            log.error(f"[Discovery] Port scan error: {e}")
            return []
    
    def get_device_info(self, ip: str) -> Optional[DiscoveredDevice]:
        """Get Roku device info from ECP endpoint"""
        try:
            resp = requests.get(f"http://{ip}:8060/query/device-info", timeout=2)
            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                
                serial = root.find('.//serial-number')
                model = root.find('.//model-name')
                manufacturer = root.find('.//vendor-name')
                name = root.find('.//user-device-name')
                
                return DiscoveredDevice(
                    ip=ip,
                    device_type=self.device_type,
                    serial_number=serial.text if serial is not None else None,
                    model=model.text if model is not None else None,
                    manufacturer=manufacturer.text if manufacturer is not None else None,
                    friendly_name=name.text if name is not None else None,
                    raw_info={'xml': resp.text}
                )
        except Exception as e:
            log.debug(f"[Discovery] Failed to get Roku info from {ip}: {e}")
        return None


class AndroidTVDiscoveryProtocol(DeviceDiscoveryProtocol):
    """Android TV discovery via SSDP"""
    
    @property
    def ssdp_search_target(self) -> str:
        return "urn:dial-multiscreen-org:service:dial:1"
    
    @property
    def device_type(self) -> str:
        return "androidtv"
    
    def get_device_info(self, ip: str) -> Optional[DiscoveredDevice]:
        """Get Android TV device info"""
        # Android TV uses DIAL protocol - implementation can be added here
        return DiscoveredDevice(
            ip=ip,
            device_type=self.device_type,
            serial_number=None,  # Would need ADB or specific protocol
            model=None,
            manufacturer=None
        )


class NetworkDeviceDiscovery:
    """Main network device discovery system"""
    
    def __init__(self):
        self.protocols: List[DeviceDiscoveryProtocol] = [
            RokuDiscoveryProtocol(),
            # AndroidTVDiscoveryProtocol(),  # Can be enabled when needed
        ]
    
    async def discover_devices(self, device_types: List[str] = None, timeout: int = SSDP_TIMEOUT) -> List[DiscoveredDevice]:
        """
        Discover devices on the network
        Tries port scanning first (works in Docker), falls back to SSDP
        """
        protocols_to_use = self.protocols
        if device_types:
            protocols_to_use = [p for p in self.protocols if p.device_type in device_types]
        
        discovered = []
        for protocol in protocols_to_use:
            # Try port scan first for Roku (works in Docker)
            if protocol.device_type == 'roku' and hasattr(protocol, 'scan_network_for_roku'):
                log.info(f"[Discovery] Scanning network for {protocol.device_type} devices...")
                ips = await protocol.scan_network_for_roku()
                for ip in ips:
                    device = protocol.get_device_info(ip)
                    if device:
                        discovered.append(device)
                        log.info(f"[Discovery] Found {protocol.device_type} via port scan: {ip} (serial: {device.serial_number})")
            
            # If no devices found via port scan, try SSDP
            if not discovered:
                log.info(f"[Discovery] Port scan found nothing, trying SSDP for {protocol.device_type}...")
                # Run blocking SSDP in thread pool
                devices = await run_blocking(lambda: self._ssdp_discover(protocol, timeout))
                discovered.extend(devices)
        
        # Cache results in Redis if available
        try:
            from app.settings import GlobalResources
            redis = GlobalResources.redis_client
            if redis and discovered:
                for d in discovered:
                    if d.serial_number:
                        key = f"discovery:serial:{d.serial_number}"
                        redis.setex(key, 86400, d.ip) # Cache for 24h
                        log.debug(f"[Discovery] Cached {d.serial_number} -> {d.ip}")
        except Exception as e:
            log.warning(f"[Discovery] Cache write error: {e}")

        return discovered
    
    def _ssdp_discover(self, protocol: DeviceDiscoveryProtocol, timeout: int) -> List[DiscoveredDevice]:
        # SSDP relies on UDP sockets which are technically blocking unless using asyncio datagram endpoint
        # However, we set a short timeout. For true async, we should use loop.sock_recv (not available for UDP)
        # or create_datagram_endpoint.
        # Given SSDP is fallback and timeout is short (3s), we can run it in executor to avoid blocking loop.
        # But for now let's keep it simple as it is rarely hit if port scan works.
        pass # Not modifying implementation, just comment.
        """Perform SSDP discovery for a specific protocol"""
        discovered = []
        seen_ips = set()
        
        ssdp_request = (
            f'M-SEARCH * HTTP/1.1\r\n'
            f'HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n'
            f'MAN: "ssdp:discover"\r\n'
            f'MX: 1\r\n'
            f'ST: {protocol.ssdp_search_target}\r\n'
            f'\r\n'
        )
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        
        try:
            sock.sendto(ssdp_request.encode('utf-8'), (SSDP_ADDR, SSDP_PORT))
            
            while True:
                try:
                    data, addr = sock.recvfrom(4096)
                    response = data.decode('utf-8', errors='ignore')
                    
                    # Extract location URL
                    for line in response.split('\r\n'):
                        if line.lower().startswith('location:'):
                            location = line.split(':', 1)[1].strip()
                            if '//' in location:
                                ip = location.split('//')[1].split(':')[0]
                                
                                # Avoid duplicates
                                if ip in seen_ips:
                                    continue
                                seen_ips.add(ip)
                                
                                # Get detailed device info
                                device = protocol.get_device_info(ip)
                                if device:
                                    discovered.append(device)
                                    log.info(f"[Discovery] Found {protocol.device_type}: {ip} (serial: {device.serial_number})")
                            break
                            
                except socket.timeout:
                    break
                    
        except Exception as e:
            log.error(f"[Discovery] SSDP error for {protocol.device_type}: {e}")
        finally:
            sock.close()
        
        return discovered
    
    async def find_device_by_attributes(self, entity_attributes: Dict, device_types: List[str] = None) -> Optional[DiscoveredDevice]:
        """
        Find a device by matching HA entity attributes to discovered devices.
        Checks Redis cache first.
        """
        # 1. Check Cache first
        try:
            from app.settings import GlobalResources
            redis = GlobalResources.redis_client
            
            serial_keys = ['serial_number', 'serial', 'unique_id']
            target_serial = None
            for key in serial_keys:
                if key in entity_attributes and entity_attributes[key]:
                    target_serial = str(entity_attributes[key]).strip()
                    break
            
            if redis and target_serial:
                cached_ip = redis.get(f"discovery:serial:{target_serial}")
                if cached_ip:
                    ip = cached_ip.decode()
                    log.info(f"[Discovery] Cache Hit for {target_serial}: {ip}")
                    # Quick verify logic could go here (ping), but assuming cache valid for speed
                    # Construct a dummy device object or verify?
                    # We should verify it is still a Roku.
                    # For now, trust cache but fallback if connection fails later.
                    return DiscoveredDevice(ip=ip, device_type='cached', serial_number=target_serial)
        except Exception as e:
            log.warning(f"[Discovery] Cache read error: {e}")
            
        # 2. Perform Discovery (Async)
        devices = await self.discover_devices(device_types=device_types)
        
        if not devices:
            log.warning("[Discovery] No devices discovered")
            return None
        
        # Try to match by serial number
        if target_serial:
            for device in devices:
                if device.serial_number and device.serial_number.strip() == target_serial:
                    log.info(f"[Discovery] Matched device by serial: {device.ip}")
                    return device
        
        # If only one device of requested type(s), return it
        if len(devices) == 1:
            log.warning(f"[Discovery] Only one device found, assuming it's the target: {devices[0].ip}")
            return devices[0]
        
        log.error(f"[Discovery] Could not match entity to any of {len(devices)} discovered devices")
        return None


# Global discovery instance
_discovery = NetworkDeviceDiscovery()


async def discover_roku_ip(entity_attributes: Dict) -> Optional[str]:
    """
    Convenience function to discover Roku IP address
    
    Args:
        entity_attributes: HA entity attributes
    
    Returns:
        IP address or None
    """
    device = await _discovery.find_device_by_attributes(entity_attributes, device_types=['roku'])
    return device.ip if device else None
