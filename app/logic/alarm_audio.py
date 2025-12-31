# app/logic/alarm_audio.py
import os
import json
import asyncio
import time
from typing import Dict, List, Optional
from app.settings import ALARM_KEYWORDS_PATH, ALARM_SOUNDS_DIR, log, run_blocking, HA_URL
from .media_ops import execute_ha_service, get_active_media_players, get_available_media_players, get_last_entity, get_entity_state

class AlarmAudioManager:
    def __init__(self):
        self.config_cache = {}
        self.last_load = 0

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

        # Priority 3: Last Used Entity (Follow Me Behavior)
        # This ensures the alarm rings where the user was last active, rather than the origin (which might be a server/dashboard)
        if not targets:
            last_entity = get_last_entity(redis_client, user_creds.get("user"))
            if last_entity and last_entity.startswith("media_player."):
                 targets.append(last_entity)
                 log.info(f"Alarm Target: Defaulting to last known entity: {last_entity}")

        # Priority 4: Default Fallback (NO LONGER BROADCASTS TO ALL)
        if not targets:
             # We no longer fall back to 'all' devices to prevent house-wide disturbance.
             # If we can't find a target, we log an error.
             log.error(f"Alarm FAILURE: No suitable targeted media player found for alarm '{title}'. Origin: {origin}, Explicit: {target_explicit}")
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
            # Step 0: Check if media is currently playing & Pause
            was_playing = False
            try:
                initial_state = await get_entity_state(target, user_creds)
                was_playing = initial_state.get("state") == "playing"
                
                if was_playing:
                    log.info(f"Music is playing on {target}, pausing for alarm...")
                    await execute_ha_service(
                        "media_player", "media_pause", target, user_creds, {}, redis_client
                    )
                    await asyncio.sleep(0.5)
            except Exception as e:
                log.warning(f"Failed to pause media on {target} before alarm: {e}")

            # Step A: TTS (Using Piper per user request)
            try:
                # Using 'tts.speak' which is the modern standard for Piper/Whisper
                # Targeting 'tts.piper' provider explicitly
                await execute_ha_service(
                    "tts", "speak", "tts.piper", user_creds,
                    {
                        "media_player_entity_id": target,
                        "message": tts_msg
                    }, 
                    redis_client
                )
                await asyncio.sleep(5) # Wait for speech to finish
            except Exception as e:
                log.error(f"Piper TTS Exception for alarm '{title}' on {target}: {e}")

            # Step B: Sound Loop (Fixed for Google Cast 500 Error)
            base_url = HA_URL.rstrip('/') if HA_URL else ""
            
            # Construct Absolute URL if path is relative/local
            if base_url and ALARM_SOUNDS_DIR.startswith("/local"):
                 full_path = f"{base_url}{ALARM_SOUNDS_DIR}/{sound_file}"
            else:
                 # Fallback to raw path if no base URL or not a local path
                 full_path = os.path.join(ALARM_SOUNDS_DIR, sound_file)

            try:
                for i in range(repeat):
                    result = await execute_ha_service(
                        "media_player", "play_media", target, user_creds,
                        {"media_content_id": full_path, "media_content_type": "music"},
                        redis_client
                    )
                    
                    if result.get("status") == "FAILURE":
                        log.warning(f"Alarm Playback Failed on {target}: {result.get('message')}")
                        break 

                    await asyncio.sleep(3)

            except Exception as e:
                log.error(f"Error during alarm loop on {target}: {e}")
            
            # Step C: Resume music if it was playing
            if was_playing:
                try:
                    log.info(f"Resuming music on {target}...")
                    await execute_ha_service(
                        "media_player", "media_play", target, user_creds, {}, redis_client
                    )
                except Exception as e:
                    log.warning(f"Failed to resume music on {target}: {e}")

audio_manager = AlarmAudioManager()
