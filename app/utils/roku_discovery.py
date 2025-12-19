"""
Roku Device Discovery using SSDP (Simple Service Discovery Protocol)
Discovers Roku devices on the local network and caches their IP addresses
"""
import socket
import logging
from typing import Optional, Dict
import xml.etree.ElementTree as ET
import requests

log = logging.getLogger(__name__)

# SSDP constants
SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SSDP_MX = 1
SSDP_ST = "roku:ecp"

def discover_roku_devices(timeout: int = 3) -> Dict[str, str]:
    """
    Discover Roku devices on the network using SSDP
    Returns dict mapping serial_number -> IP address
    """
    discovered = {}
    
    ssdp_request = (
        f'M-SEARCH * HTTP/1.1\r\n'
        f'HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n'
        f'MAN: "ssdp:discover"\r\n'
        f'MX: {SSDP_MX}\r\n'
        f'ST: {SSDP_ST}\r\n'
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
                
                # Parse SSDP response
                if 'roku' in response.lower():
                    # Extract location URL
                    for line in response.split('\r\n'):
                        if line.lower().startswith('location:'):
                            location = line.split(':', 1)[1].strip()
                            # Parse IP from location URL
                            if '//' in location:
                                ip = location.split('//')[1].split(':')[0]
                                
                                # Get device info from Roku
                                serial = _get_roku_serial(ip)
                                if serial:
                                    discovered[serial] = ip
                                    log.info(f"[Roku Discovery] Found Roku: {serial} at {ip}")
                            break
                            
            except socket.timeout:
                break
                
    except Exception as e:
        log.error(f"[Roku Discovery] Error: {e}")
    finally:
        sock.close()
    
    return discovered

def _get_roku_serial(ip: str) -> Optional[str]:
    """Get Roku serial number from device-info endpoint"""
    try:
        resp = requests.get(f"http://{ip}:8060/query/device-info", timeout=2)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            serial = root.find('.//serial-number')
            if serial is not None and serial.text:
                return serial.text
    except Exception as e:
        log.debug(f"[Roku Discovery] Could not get serial from {ip}: {e}")
    return None

def find_roku_ip_by_entity(entity_id: str, entity_attributes: Dict) -> Optional[str]:
    """
    Find Roku IP address by matching entity info to discovered devices
    Tries to match by serial number from entity attributes
    """
    # Check if entity has serial number attribute
    serial_keys = ['serial_number', 'serial', 'device_id', 'unique_id']
    entity_serial = None
    
    for key in serial_keys:
        if key in entity_attributes and entity_attributes[key]:
            entity_serial = str(entity_attributes[key])
            break
    
    if not entity_serial:
        log.warning(f"[Roku Discovery] No serial number found in entity {entity_id} attributes")
        return None
    
    # Discover Roku devices
    devices = discover_roku_devices()
    
    # Try to match by serial
    if entity_serial in devices:
        ip = devices[entity_serial]
        log.info(f"[Roku Discovery] Matched {entity_id} to {ip} via serial {entity_serial}")
        return ip
    
    # If only one Roku found, return it (fallback for single-Roku setups)
    if len(devices) == 1:
        ip = list(devices.values())[0]
        log.warning(f"[Roku Discovery] Only one Roku found, assuming {entity_id} is {ip}")
        return ip
    
    log.error(f"[Roku Discovery] Could not match {entity_id} to any discovered Roku")
    return None
