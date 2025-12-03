# app/logic/alarm_audio.py
import os
import json
import asyncio
import time
from typing import Dict, List, Optional
from settings import ALARM_KEYWORDS_PATH, ALARM_SOUNDS_DIR, log, run_blocking
from .media_ops import execute_ha_service, get_active_media_players

class AlarmAudioManager:
    def __init__(self):
        self.config_cache = {}
        self.last_load = 0

    def _load_config(self):
        """Load alarm keywords config from JSON, with error handling."""
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
        """Determine sound file and repeat count based on title keywords."""
        # Reload config if older than 60s
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
        """
        Orchestrates the alarm playback:
        1. TTS Announcement
        2. Sound File Loop (with error handling)
        """
        title = timer.get("title", "Alarm")
        origin = timer.get("origin_device")

        # 1. Determine Target Devices
        targets = []

        if origin and origin.startswith("media_player."):
            targets.append(origin)
        elif origin and origin.split('.')[0] in ['light', 'switch', 'sensor']:
            log.warning(f"Alarm Origin Device '{origin}' is non-media. Falling back to active media players.")

        # Fallback: Get active media players if none from origin
        if not targets:
            active = await get_active_media_players(user_creds)
            if active:
                targets.extend(active)

        # If still no targets, log and abort
        if not targets:
            log.error(f"Alarm FAILURE: No suitable media player found for alarm '{title}'. Origin: {origin}")
            return

        # Remove duplicates
        targets = list(set(targets))

        # Get audio settings
        settings = self.get_sound_settings(title)
        sound_file = settings.get("sound")
        repeat = settings.get("repeat", 3)

        # TTS message
        tts_msg = f"Attention. Alarm for {title}."
        if "timer" in title.lower():
            tts_msg = f"Your {title} has finished."

        log.info(f"Triggering Alarm '{title}' on {targets}. Sound: {sound_file} x{repeat}")

        # Play TTS and sound loop on each target
        for target in targets:
            domain = target.split('.')[0]

            # Step A: TTS
            try:
                await execute_ha_service(
                    "media_player", "play_media", target, user_creds,
                    {
                        "media_content_id": tts_msg,
                        "media_content_type": "text"
                    },
                    redis_client
                )
            except Exception as e:
                log.error(f"TTS Failed for alarm '{title}' on {target}: {e}")

            # Wait briefly for TTS to finish
            await asyncio.sleep(4)

            # Step B: Sound Loop
            full_path = os.path.join(ALARM_SOUNDS_DIR, sound_file)
            try:
                for i in range(repeat):
                    result = await execute_ha_service(
                        "media_player", "play_media", target, user_creds,
                        {
                            "media_content_id": full_path,
                            "media_content_type": "music"
                        },
                        redis_client
                    )

                    # Handle explicit failure
                    if result.get("status") == "FAILURE":
                        log.warning(f"Alarm Sound Playback Failed for '{sound_file}' on {target}: {result.get('message')}")
                        log.info("Aborting sound loop, relying on TTS.")
                        break

                    # Wait between loops (~2-3s)
                    await asyncio.sleep(3)

            except Exception as e:
                log.error(f"Critical error during alarm sound loop on {target}: {e}")

    def cancel_playback(self, timer_id: str):
        """
        Optional: Placeholder for future cancellation logic.
        Currently, HA playback cannot always be interrupted programmatically.
        """
        log.info(f"Cancel playback requested for timer {timer_id}, no-op for now.")

# Singleton instance
audio_manager = AlarmAudioManager()

