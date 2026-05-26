# services/execution/presence.py
"""
ESPresense BLE presence detection pipeline.

Subscribes to ESPresense MQTT topics and publishes room-level presence
to Redis under `ha:presence:{user_id}` keys.

MQTT Topic Pattern (ESPresense default):
  espresense/devices/{mac_address} -> JSON with rssi, room, etc.
  espresense/rooms/{room_name} -> JSON with occupants

Redis Key Pattern:
  ha:presence:{user_id} -> JSON: {"room": "living_room", "confidence": 0.85, "last_seen": 1234567890}
  ha:presence:all -> JSON: {user_id: {...}, ...}
"""
import asyncio
import json
import logging
import time
from typing import Dict, Any, Optional, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    import paho.mqtt.client  # pyright: ignore[reportMissingImports]
    import redis.asyncio as aioredis

try:
    import paho.mqtt.client as mqtt  # pyright: ignore[reportMissingImports]
    import redis.asyncio as aioredis
except ImportError:
    paho = None
    aioredis = None  # type: ignore[assignment]

log = logging.getLogger("execution.presence")

# Default MQTT settings
DEFAULT_MQTT_HOST = "localhost"
DEFAULT_MQTT_PORT = 1883
DEFAULT_MQTT_TOPIC = "espresense/rooms/#"
DEFAULT_REDIS_KEY_PREFIX = "ha:presence:"
DEFAULT_PRESENCE_TTL = 300  # 5 minutes


class PresenceTracker:
    """Tracks BLE presence from ESPresense MQTT and syncs to Redis."""

    def __init__(
        self,
        mqtt_host: str = DEFAULT_MQTT_HOST,
        mqtt_port: int = DEFAULT_MQTT_PORT,
        mqtt_topic: str = DEFAULT_MQTT_TOPIC,
        redis_url: str = "redis://localhost:6379/0",
        redis_key_prefix: str = DEFAULT_REDIS_KEY_PREFIX,
        presence_ttl: int = DEFAULT_PRESENCE_TTL,
    ):
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mqtt_topic = mqtt_topic
        self.redis_url = redis_url
        self.redis_key_prefix = redis_key_prefix
        self.presence_ttl = presence_ttl
        self._redis: Optional["aioredis.Redis"] = None  # type: ignore[valid-type]
        self._mqtt_client = None
        self._running = False
        self._user_mac_map: Dict[str, str] = {}  # user_id -> mac_address
        self._callbacks: list[Callable] = []

    async def start(self):
        """Start MQTT subscriber and Redis connection."""
        if paho is None or aioredis is None:
            log.warning("[presence] paho-mqtt or redis not installed, presence tracking disabled")
            return

        self._running = True
        try:
            self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
            assert self._redis is not None
            await self._redis.ping()
            log.info("[presence] Connected to Redis")
        except Exception as e:
            log.warning(f"[presence] Redis connection failed: {e}")
            self._redis = None
            return

        try:
            self._mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            self._mqtt_client.on_connect = self._on_mqtt_connect
            self._mqtt_client.on_message = self._on_mqtt_message
            self._mqtt_client.connect(self.mqtt_host, self.mqtt_port, 60)
            self._mqtt_client.loop_start()
            log.info(f"[presence] MQTT connected to {self.mqtt_host}:{self.mqtt_port}")
        except Exception as e:
            log.warning(f"[presence] MQTT connection failed: {e}")
            self._mqtt_client = None

    async def stop(self):
        """Stop MQTT subscriber and close Redis connection."""
        self._running = False
        if self._mqtt_client:
            self._mqtt_client.loop_stop()
            self._mqtt_client.disconnect()
        if self._redis:
            await self._redis.close()

    def register_user_mac(self, user_id: str, mac_address: str):
        """Register a user's BLE MAC address for tracking."""
        self._user_mac_map[user_id] = mac_address.lower()
        log.info(f"[presence] Registered user {user_id} -> MAC {mac_address}")

    def on_presence_change(self, callback: Callable):
        """Register a callback for presence changes. Callback receives (user_id, room, confidence)."""
        self._callbacks.append(callback)

    def _on_mqtt_connect(self, client, userdata, flags, reason_code, properties):
        """Handle MQTT connection."""
        log.info(f"[presence] Subscribed to {self.mqtt_topic}")
        client.subscribe(self.mqtt_topic)

    def _on_mqtt_message(self, client, userdata, msg):
        """Handle incoming MQTT message."""
        try:
            payload = json.loads(msg.payload.decode())
            self._process_message(msg.topic, payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            log.warning(f"[presence] Failed to parse MQTT message: {e}")

    def _process_message(self, topic: str, payload: dict):
        """Process ESPresense MQTT message and update Redis."""
        if "espresense/rooms/" in topic:
            room_name = topic.split("/")[-1]
            self._process_room_update(room_name, payload)
        elif "espresense/devices/" in topic:
            mac = topic.split("/")[-1].lower()
            self._process_device_update(mac, payload)

    def _process_room_update(self, room_name: str, payload: dict):
        """Process room-level presence update."""
        occupants = payload.get("occupants", [])
        for occupant in occupants:
            user_id = occupant.get("id") or occupant.get("name")
            confidence = occupant.get("confidence", 0.5)
            if user_id:
                try:
                    asyncio.create_task(
                        self._update_presence(user_id, room_name, confidence)
                    )
                except RuntimeError:
                    # No running event loop (e.g., in tests)
                    pass

    def _process_device_update(self, mac: str, payload: dict):
        """Process device-level presence update."""
        user_id = None
        for uid, device_mac in self._user_mac_map.items():
            if mac == device_mac:
                user_id = uid
                break

        if user_id:
            room = payload.get("room", "unknown")
            confidence = payload.get("confidence", 0.5)
            try:
                asyncio.create_task(
                    self._update_presence(user_id, room, confidence)
                )
            except RuntimeError:
                # No running event loop (e.g., in tests)
                pass

    async def _update_presence(self, user_id: str, room: str, confidence: float):
        """Update presence in Redis."""
        if not self._redis:
            return

        presence_data = {
            "room": room,
            "confidence": round(confidence, 3),
            "last_seen": int(time.time()),
            "source": "espresense",
        }

        try:
            key = f"{self.redis_key_prefix}{user_id}"
            await self._redis.set(key, json.dumps(presence_data), ex=self.presence_ttl)

            # Update global presence index
            await self._redis.hset(
                f"{self.redis_key_prefix}all", user_id, json.dumps(presence_data)
            )

            log.info(f"[presence] {user_id} -> {room} (confidence={confidence})")

            # Notify callbacks
            for callback in self._callbacks:
                try:
                    callback(user_id, room, confidence)
                except Exception as e:
                    log.warning(f"[presence] Callback error: {e}")
        except Exception as e:
            log.warning(f"[presence] Redis update failed: {e}")

    async def get_user_presence(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get current presence data for a user."""
        if not self._redis:
            return None

        try:
            key = f"{self.redis_key_prefix}{user_id}"
            data = await self._redis.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            log.warning(f"[presence] Redis get failed: {e}")
            return None

    async def get_all_presence(self) -> Dict[str, Dict[str, Any]]:
        """Get presence data for all tracked users."""
        if not self._redis:
            return {}

        try:
            data = await self._redis.hgetall(f"{self.redis_key_prefix}all")
            return {k: json.loads(v) for k, v in data.items()}
        except Exception as e:
            log.warning(f"[presence] Redis hgetall failed: {e}")
            return {}

    async def get_rooms(self) -> list[str]:
        """Get list of all known rooms."""
        if not self._redis:
            return []

        try:
            all_presence = await self.get_all_presence()
            rooms = set()
            for data in all_presence.values():
                room = data.get("room")
                if room and room != "unknown":
                    rooms.add(room)
            return sorted(rooms)
        except Exception:
            return []


# Global presence tracker instance
_presence_tracker: Optional[PresenceTracker] = None


def get_presence_tracker() -> PresenceTracker:
    """Get or create the global presence tracker instance."""
    global _presence_tracker
    if _presence_tracker is None:
        _presence_tracker = PresenceTracker()
    return _presence_tracker


async def init_presence_tracker(
    mqtt_host: str = DEFAULT_MQTT_HOST,
    mqtt_port: int = DEFAULT_MQTT_PORT,
    redis_url: str = "redis://localhost:6379/0",
) -> PresenceTracker:
    """Initialize the global presence tracker."""
    global _presence_tracker
    _presence_tracker = PresenceTracker(
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        redis_url=redis_url,
    )
    await _presence_tracker.start()
    return _presence_tracker
