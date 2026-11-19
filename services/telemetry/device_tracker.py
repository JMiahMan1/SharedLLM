"""Network device presence tracking and notifications.

Monitors the router_monitor's network state for device arrival/departure events,
matches MAC addresses to registered devices, and triggers notifications and
automation actions. Also integrates with BLE room presence system for
room-level location tracking.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, UTC
from typing import Any

import aiohttp
import redis.asyncio as redis

from services.common.secrets import secret_matches
from services.config import (
    CONTROL_PLANE_URL,
    EXECUTION_SVC,
    IDENTITY_SVC,
    INTERNAL_SECRET,
    REDIS_URL,
)

log = logging.getLogger("telemetry.device_tracker")

# Redis keys
DEVICE_REGISTRY_PREFIX = "device:registry:"
DEVICE_HISTORY_PREFIX = "device:history:"
DEVICE_PRESENCE_PREFIX = "device:presence:"
DEVICE_HISTORY_MAX = 1000  # max history entries per device
DEVICE_HISTORY_TTL = 90 * 86400  # 90 days

# Polling interval
POLL_INTERVAL = int(os.getenv("DEVICE_TRACKER_POLL_SECONDS", "30"))
# How long a device must be absent before considered "away"
AWAY_THRESHOLD = int(os.getenv("DEVICE_AWAY_THRESHOLD_SECONDS", "300"))  # 5 minutes

# Room presence integration
ROOM_PRESENCE_MAX_AGE_S = float(os.getenv("ROOM_PRESENCE_MAX_AGE_S", "600"))


class DeviceTracker:
    """Monitors network device presence and triggers notifications/automations."""
    
    def __init__(self, redis_client: redis.Redis, http_session: aiohttp.ClientSession):
        self.r = redis_client
        self.session = http_session
        self.prev_known_macs: set[str] = set()
        self.prev_state: dict[str, Any] | None = None
        self._running = True
    
    async def _get_device_registry(self, mac: str) -> dict[str, Any] | None:
        """Get device registry entry by MAC."""
        key = f"{DEVICE_REGISTRY_PREFIX}{mac.lower()}"
        data = await self.r.get(key)
        if data:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return None
        return None
    
    async def _get_all_device_registry(self) -> dict[str, dict[str, Any]]:
        """Get all device registry entries (MAC -> device info)."""
        # For now, we'll scan keys - could be optimized with a set index
        devices = {}
        try:
            async for key in self.r.scan_iter(f"{DEVICE_REGISTRY_PREFIX}*"):
                data = await self.r.get(key)
                if data:
                    try:
                        device = json.loads(data)
                        mac = key.decode().split(":")[-1] if isinstance(key, bytes) else key.split(":")[-1]
                        devices[mac.lower()] = device
                    except (json.JSONDecodeError, IndexError):
                        continue
        except Exception as e:
            log.warning(f"Failed to scan device registry: {e}")
        return devices
    
    async def _record_history(self, mac: str, event: dict[str, Any]) -> None:
        """Record a device event to history."""
        key = f"{DEVICE_HISTORY_PREFIX}{mac.lower()}"
        event["timestamp"] = datetime.now(UTC).isoformat()
        pipe = self.r.pipeline()
        pipe.lpush(f"{DEVICE_HISTORY_PREFIX}{mac.lower()}", json.dumps(event))
        pipe.ltrim(f"{DEVICE_HISTORY_PREFIX}{mac.lower()}", 0, DEVICE_HISTORY_MAX - 1)
        pipe.expire(f"{DEVICE_HISTORY_PREFIX}{mac.lower()}", DEVICE_HISTORY_TTL)
        await pipe.execute()
    
    async def _update_presence(self, mac: str, present: bool, info: dict[str, Any] | None = None) -> None:
        """Update device presence state."""
        key = f"{DEVICE_PRESENCE_PREFIX}{mac.lower()}"
        state = {
            "present": present,
            "last_update": datetime.now(UTC).isoformat(),
        }
        if info:
            state.update({k: v for k, v in info.items() if v is not None})
        await self.r.set(key, json.dumps(state))
    
    async def _get_current_network_state(self) -> dict[str, Any] | None:
        """Fetch current network state from control plane."""
        try:
            url = f"{CONTROL_PLANE_URL}/api/network/status"
            async with self.session.get(
                url,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            log.warning(f"Failed to fetch network status: {e}")
        return None
    
    async def _extract_devices_from_snap(self, snap: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Extract all known devices from a network snapshot."""
        devices = {}
        
        # From DHCP leases
        for lease in snap.get("leases", []):
            mac = (lease.get("mac") or "").lower()
            if mac:
                devices[mac] = {
                    "mac": mac,
                    "ip": lease.get("ipaddr"),
                    "hostname": lease.get("hostname") or "",
                    "source": "dhcp",
                }
        
        # From static hosts (DHCP reservations)
        for mac, name in snap.get("hosts", {}).items():
            mac = mac.lower()
            if mac in devices:
                devices[mac]["hostname"] = name
            else:
                devices[mac] = {"mac": mac, "hostname": name, "source": "static"}
        
        # From Wi-Fi clients
        for radio in snap.get("radios", []):
            for client in radio.get("clients", []):
                mac = (client.get("mac") or "").lower()
                if mac:
                    if mac in devices:
                        devices[mac].update({
                            "signal": client.get("signal"),
                            "rx_kbps": client.get("rx_kbps"),
                            "tx_kbps": client.get("tx_kbps"),
                            "connected_s": client.get("connected_s"),
                            "ssid": radio.get("ssid"),
                        })
                    else:
                        devices[mac] = {
                            "mac": mac,
                            "signal": client.get("signal"),
                            "rx_kbps": client.get("rx_kbps"),
                            "tx_kbps": client.get("tx_kbps"),
                            "connected_s": client.get("connected_s"),
                            "ssid": radio.get("ssid"),
                            "source": "wifi",
                        }
        
        return devices
    
    async def _send_notification(self, device: dict[str, Any], event_type: str, snap_info: dict[str, Any] | None = None) -> None:
        """Send push/email notification for device event."""
        user_id = device.get("user_id")
        if not user_id:
            return
        
        name = device.get("name") or device.get("mac")
        mac = device.get("mac")
        
        if event_type == "arrival":
            title = f"{name} arrived home"
            body = f"{name} ({mac}) connected to the network"
        elif event_type == "departure":
            title = f"{name} left home"
            body = f"{name} ({mac}) disconnected from the network"
        else:
            return
        
        # Send push notification
        if device.get("notify_on_arrival") and event_type == "arrival":
            await self._send_push(user_id, title, body, {"device_mac": mac, "event": event_type})
        elif device.get("notify_on_departure") and event_type == "departure":
            await self._send_push(user_id, title, body, {"device_mac": mac, "event": event_type})
        
        # Send email
        if device.get("email_on_arrival") and event_type == "arrival":
            await self._send_email(user_id, title, body, mac)
        elif device.get("email_on_departure") and event_type == "departure":
            await self._send_email(user_id, title, body, mac)
    
    async def _send_push(self, user_id: str, title: str, body: str, data: dict[str, Any]) -> None:
        """Send push notification via telemetry push service."""
        try:
            url = f"{CONTROL_PLANE_URL}/api/push/send"
            payload = {
                "user": user_id,
                "title": title,
                "body": body,
                "data": data,
            }
            async with self.session.post(
                url,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    log.warning(f"Push notification failed: {resp.status}")
        except Exception as e:
            log.warning(f"Failed to send push: {e}")
    
    async def _send_email(self, user_id: str, title: str, body: str, mac: str) -> None:
        """Send email notification via email service."""
        try:
            url = f"{CONTROL_PLANE_URL}/api/email/send"
            payload = {
                "user_id": user_id,
                "subject": title,
                "body": body,
                "device_mac": mac,
            }
            async with self.session.post(
                f"{CONTROL_PLANE_URL}/api/email/send",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    log.warning(f"Email notification failed: {resp.status}")
        except Exception as e:
            log.warning(f"Failed to send email: {e}")
    
    async def _get_room_presence(self, user_id: str) -> dict[str, Any] | None:
        """Query room presence from execution service."""
        try:
            url = f"{EXECUTION_SVC}/api/room_presence"
            payload = {"user_id": user_id}
            async with self.session.post(
                url,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            log.warning(f"Failed to get room presence for {user_id}: {e}")
        return None
    
    async def _trigger_room_automation(self, mac: str, event_type: str, device: dict[str, Any], room_info: dict[str, Any] | None) -> None:
        """Trigger room-level automation based on BLE presence."""
        try:
            user_id = device.get("user_id")
            if not user_id:
                return
            
            # Build room-aware automation payload
            payload = {
                "trigger": f"device_{event_type}",
                "device_mac": mac,
                "device_name": device.get("name") or mac,
                "event_type": event_type,
                "device_info": {
                    "ip": device.get("ip"),
                    "hostname": device.get("hostname"),
                    "device_type": device.get("device_type"),
                    "user_id": device.get("user_id"),
                },
                "room_info": room_info,  # BLE room presence data
                "actions": device.get("automation_actions", []),
            }
            
            # Add event-specific context
            if event_type == "arrival":
                payload["actions"].extend([
                    {"type": "turn_on_lights", "area": "entryway", "condition": "after_sunset"},
                    {"type": "update_room_presence", "device_mac": mac, "status": "home"},
                    {"type": "trigger_ble_scan", "device_mac": mac, "purpose": "locate"},
                ])
                # Add room-specific actions
                if room_info and room_info.get("room"):
                    payload["actions"].append({
                        "type": "turn_on_lights",
                        "area": room_info["room"],
                        "condition": "after_sunset"
                    })
            elif event_type == "departure":
                payload["actions"].extend([
                    {"type": "turn_off_lights", "area": "all", "condition": "no_one_home"},
                    {"type": "update_room_presence", "device_mac": mac, "status": "away"},
                    {"type": "arm_security", "mode": "away", "condition": "no_one_home"},
                ])
                if room_info and room_info.get("room"):
                    payload["actions"].append({
                        "type": "turn_off_lights",
                        "area": room_info["room"],
                        "condition": "room_empty"
                    })
            
            # Send to automation service (which calls execution trigger)
            url = f"{CONTROL_PLANE_URL}/api/automation/trigger"
            async with self.session.post(
                url,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    log.warning(f"Automation trigger failed: {resp.status}")
        except Exception as e:
            log.warning(f"Failed to trigger room automation: {e}")
    
    async def _trigger_basic_automation(self, mac: str, event_type: str, device: dict[str, Any]) -> None:
        """Fallback basic automation without room info."""
        try:
            payload = {
                "trigger": f"device_{event_type}",
                "device_mac": mac,
                "device_name": device.get("name") or mac,
                "event_type": event_type,
                "device_info": {
                    "ip": device.get("ip"),
                    "hostname": device.get("hostname"),
                    "device_type": device.get("device_type"),
                    "user_id": device.get("user_id"),
                },
                "actions": device.get("automation_actions", []),
            }
            
            if event_type == "arrival":
                payload["actions"].extend([
                    {"type": "turn_on_lights", "area": "entryway", "condition": "after_sunset"},
                    {"type": "trigger_ble_scan", "device_mac": mac, "purpose": "locate"},
                    {"type": "update_room_presence", "device_mac": mac, "status": "home"},
                ])
            elif event_type == "departure":
                payload["actions"].extend([
                    {"type": "turn_off_lights", "area": "all", "condition": "no_one_home"},
                    {"type": "update_room_presence", "device_mac": mac, "status": "away"},
                    {"type": "arm_security", "mode": "away", "condition": "no_one_home"},
                ])
            
            url = f"{CONTROL_PLANE_URL}/api/automation/trigger"
            async with self.session.post(
                url,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    log.warning(f"Automation trigger failed: {resp.status}")
        except Exception as e:
            log.warning(f"Failed to trigger automation: {e}")
    
    async def _trigger_automation(self, mac: str, event_type: str, device: dict[str, Any]) -> None:
        """Trigger automation actions for device event via execution service trigger."""
        try:
            # Get room presence for user
            user_id = device.get("user_id")
            room_info = None
            if user_id:
                room_info = await self._get_room_presence(user_id)
            
            # If we have room info, use room-level automation
            if room_info and room_info.get("status") == "known":
                await self._trigger_room_automation(mac, event_type, device, room_info)
            else:
                # Fallback to basic automation
                await self._trigger_basic_automation(mac, event_type, device)
        except Exception as e:
            log.warning(f"Failed to trigger automation: {e}")
    
    async def tick(self) -> None:
        """Main polling loop - check for device changes."""
        snap = await self._get_current_network_state()
        if not snap:
            return
        
        current_devices = await self._extract_devices_from_snap(snap)
        current_macs = set(current_devices.keys())
        
        # Get device registry
        registry = await self._get_all_device_registry()
        
        # Detect arrivals (new MACs not seen before)
        new_macs = current_macs - self.prev_known_macs
        for mac in new_macs:
            device_info = current_devices.get(mac, {})
            registry_device = await self._get_device_registry(mac)
            
            # Update presence
            await self._update_presence(mac, True, device_info)
            
            # Record history
            await self._record_history(mac, {
                "event": "arrival",
                "info": device_info,
            })
            
            # If device is registered, send notifications
            if registry_device:
                await self._send_notification(registry_device, "arrival", device_info)
                await self._trigger_automation(mac, "arrival", registry_device)
            else:
                # Unknown device - log it
                log.info(f"New device detected: {mac} - {device_info.get('hostname', 'unknown')}")
                # Could trigger "unknown device" alert here
        
        # Detect departures (MACs no longer present)
        departed_macs = self.prev_known_macs - current_macs
        for mac in departed_macs:
            registry_device = await self._get_device_registry(mac)
            
            # Only mark as away if absent for threshold
            # We'll use a delayed check or track last seen time
            last_seen_key = f"device:last_seen:{mac.lower()}"
            last_seen_str = await self.r.get(last_seen_key)
            if last_seen_str:
                last_seen = float(last_seen_str)
                if time.time() - last_seen >= AWAY_THRESHOLD:
                    # Confirm departure
                    await self._update_presence(mac, False)
                    await self._record_history(mac, {"event": "departure"})
                    
                    registry_device = await self._get_device_registry(mac)
                    if registry_device:
                        await self._send_notification(registry_device, "departure")
                        await self._trigger_automation(mac, "departure", registry_device)
            
            # Update last seen
            await self.r.set(f"device:last_seen:{mac.lower()}", str(time.time()))
        
        # Update last seen for current devices
        for mac in current_macs:
            await self.r.set(f"device:last_seen:{mac.lower()}", str(time.time()))
        
        # Update known MACs
        self.prev_known_macs = current_macs
    
    async def run(self) -> None:
        """Main loop."""
        log.info("Device tracker started")
        
        # Initial fetch to populate known MACs
        snap = await self._get_current_network_state()
        if snap:
            current_devices = await self._extract_devices_from_snap(snap)
            self.prev_known_macs = set(current_devices.keys())
            
            # Initialize last_seen for all current devices
            for mac in self.prev_known_macs:
                await self.r.set(f"device:last_seen:{mac.lower()}", str(time.time()))
                await self._update_presence(mac, True)
        
        while self._running:
            try:
                await self.tick()
            except Exception as e:
                log.error(f"Device tracker error: {e}")
            await asyncio.sleep(POLL_INTERVAL)
    
    def stop(self) -> None:
        self._running = False


async def run_device_tracker() -> None:
    """Entry point for running the device tracker as a service."""
    r = redis.from_url(REDIS_URL, decode_responses=True)
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tracker = DeviceTracker(r, session)
        try:
            await tracker.run()
        finally:
            await r.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_device_tracker())