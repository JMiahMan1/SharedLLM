# app/domains/media/integrations.py
"""
Media integration constants and helper functions.
"""

import logging
from typing import Dict

log = logging.getLogger(__name__)

# App Package IDs for Android TV Smart Routing
APP_PACKAGES = {
    "youtube": "com.google.android.youtube.tv",
    "netflix": "com.netflix.ninja",
    "disney": "com.disney.disneyplus",
    "disney+": "com.disney.disneyplus",
    "spotify": "com.spotify.tv.android",
    "prime video": "com.amazon.amazonvideo.livingroom",
    "amazon prime": "com.amazon.amazonvideo.livingroom",
    "plex": "com.plexapp.android",
    "twitch": "tv.twitch.android.app",
    "kodi": "org.xbmc.kodi",
    "hulu": "com.hulu.livingroomplus",
    "hbo": "com.wbd.stream",
    "max": "com.wbd.stream"
}

# Media intent definitions used by pipeline.py for routing
MEDIA_INTENTS = [
    "turn_on", "turn_off", "toggle",
    "stop_media", "play_media", "open_app",
    "media_next", "media_previous",
    "volume_up", "volume_down", "volume_set", "volume_mute",  # Volume controls
    "nav_up", "nav_down", "nav_left", "nav_right",
    "nav_enter", "nav_back", "nav_home",
    "set_color", "set_brightness", "dim", "brighten"
]

# Regex intent mapping used by pipeline.py
REGEX_INTENT_MAP = {
    r"\b(open|launch|start)\s+(netflix|youtube|disney|hulu|plex|prime|spotify)": "open_app",
    r"\bplay\b": "play_media",
    r"\b(stop|pause)\b": "stop_media",
    r"\b(resume|unpause)\b": "media_play",
    r"\b(skip|next)\b": "media_next",
    r"\b(previous|back|prev)\b": "media_previous",
    r"\b(scroll|move|go)\s+up\b": "nav_up",
    r"\b(scroll|move|go)\s+down\b": "nav_down",
    r"\b(scroll|move|go)\s+left\b": "nav_left",
    r"\b(scroll|move|go)\s+right\b": "nav_right",
    r"\bgo back\b|\bback\b": "nav_back",
    r"\bgo home\b|\bhome\b": "nav_home",
    r"\bselect\b|\benter\b|\bok\b": "nav_enter",
    # Color control: matches "set/change/make X color" OR "turn X to color"
    r"\b(set|change|make).+(color|colour|red|blue|green|purple|orange|yellow|pink|white|warm|cool)": "set_color",
    r"\bturn\s+.+\s+(?:to\s+)?(red|blue|green|purple|orange|yellow|pink|white|warm|cool)": "set_color",
    r"\b(dim|darken|lower)\b": "dim",
    r"\b(brighten|brighter|increase)\b": "brighten",
    r"\b(brightness|bright)\b": "set_brightness",
}
