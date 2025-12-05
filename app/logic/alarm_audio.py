# app/logic/alarm_audio.py
import os
import json
import asyncio
import time
from typing import Dict, List, Optional
from settings import ALARM_KEYWORDS_PATH, ALARM_SOUNDS_DIR, log, run_blocking, HA_URL
from .media_ops import execute_ha_service, get_active_media_players, get_available_media_players

class AlarmAudioManager:
    def __init__(self):
        self.config_cache = {}\n        self.last_load = 0

    def _load_config(self):
        if os.path.exists(ALARM_KEYWORDS_PATH):
            try:
                with open(ALARM_KEYWORDS_PATH, "r") as f:
                    self.config_cache = json.load(f)
            except Exception as e:
                log.error(f"Failed to load alarm keywords: {e}")
                self.config_cache = {}
        else:
            self.config_cache = {}

    def get_sound_settings(self, title: str) -> Dict:
        if time.time() - self.last_load > 60:
            self._load_config()
            self.last_load = time.time()

        title_lower = title.lower()
        keywords = self.config_cache.get("keywords", {})
        default = self.config_cache.get("default", {"sound": "default_alarm.wav", "repeat": 3})

        for kw, settings in keywords.items():
            if kw in title_lower:
                return settings

        return default

    async def play_alarm_sequence(self, timer: Dict, user_creds: Dict, redis_client):
        title = timer.get("title", "Alarm")
        origin = timer.get("origin_device")
        target_explicit = timer.get("target_device")

        # 1. Determine Target Devices
        targets = []

        # Priority 1: Explicit Target (e.g. "on Office TV")
        if target_explicit:
            if target_explicit.startswith("media_player."):
                targets.append(target_explicit)
            else:
                log.warning(f"Alarm target '{target_explicit}' is not a media_player. Ignoring.")

        # Priority 2: Origin Device (If valid media player)
        if not targets and origin:
            if origin.startswith("media_player."):
                targets.append(origin)
            else:
                log.warning(f"Alarm Origin Device '{origin}' is NOT a media_player. Falling back.")

        # Priority 3: Fallback to Active Players (Currently Playing/Paused)
        if not targets:
            active = await get_active_media_players(user_creds)
            if active:
                targets.extend(active)
                log.info(f"Alarm Fallback: Playing on active media players: {active}")

        # Priority 4: Fallback to ALL Available Players (Last Resort)
        if not targets:
            available = await get_available_media_players(user_creds)
            if available:
                 # Optional: Filter out groups/apps if desired
                 targets.extend(available)
                 log.info("Alarm Fallback: Broadcasting to all available media players.")

        if not targets:
            log.error(f"Alarm FAILURE: No suitable media player found for alarm '{title}'. Origin was: {origin}")
            return

        targets = list(set(targets))

        settings = self.get_sound_settings(title)
        sound_file = settings.get("sound")
        repeat = settings.get("repeat", 3)

        tts_msg = f"Attention. Alarm for {title}."
        if "timer" in title.lower():
            tts_msg = f"Your {title} has finished."

        log.info(f"Triggering Alarm '{title}' on {targets}. Sound: {sound_file} x{repeat}")

        for target in targets:
            # Step A: TTS (Wrapped in Try/Except to prevent crashes)
            try:
                result = await execute_ha_service(
                    "media_player", "play_media", target, user_creds,
                    {"media_content_id": tts_msg, "media_content_type": "text"},
                    redis_client
                )
                if result.get("status") == "FAILURE":
                     log.warning(f"TTS Failed on {target}. Continuing to sound...")
            except Exception as e:
                log.error(f"TTS Exception for alarm '{title}' on {target}: {e}")

            await asyncio.sleep(4)

            # Step B: Sound Loop (Fixed for Google Cast 500 Error)
            base_url = HA_URL.rstrip('/') if HA_URL else ""

            # Construct Absolute URL if path is relative/local
            if base_url and ALARM_SOUNDS_DIR.startswith("/local"):
                 full_path = f"{base_url}{ALARM_SOUNDS_DIR}/{sound_file}"
            else:
                 # Fallback for full URLs or other paths
                 full_path = f"{ALARM_SOUNDS_DIR}/{sound_file}"

            for i in range(repeat):
                try:
                    await execute_ha_service(
                        "media_player", "play_media", target, user_creds,
                        {"media_content_id": full_path, "media_content_type": "music"},
                        redis_client
                    )
                    await asyncio.sleep(5) # Wait for sound to play
                except Exception as e:
                    log.error(f"Sound Playback Error on {target}: {e}")
                    break

audio_manager = AlarmAudioManager()
