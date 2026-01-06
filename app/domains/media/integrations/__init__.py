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
    "stop_media", "play_media", "open_app", "media_play", "pause_media",
    "media_next", "media_previous",
    "volume_up", "volume_down", "volume_set", "volume_mute",  # Volume controls
    "nav_up", "nav_down", "nav_left", "nav_right",
    "nav_enter", "nav_back", "nav_home",
    "set_color", "set_brightness", "dim", "brighten"
]

# Regex intent mapping used by pipeline.py
REGEX_INTENT_MAP = {
    # Power commands MUST be first to prevent "turn off" from matching "play"
    r"\bturn\s+off\b": "turn_off",
    r"\bturn\s+on\b": "turn_on",  
    r"\b(open|launch|start)\s+(netflix|youtube|disney|hulu|plex|prime|spotify)": "open_app",
    # [Resume Context] "Play the video" or "Resume the movie" -> media_play (NOT search)
    r"\b(play|resume|unpause)\s+(the\s+)?(video|movie|show|film)\b": "media_play",
    
    r"\b(play|watch|view).+(video|movie|show|film|clip|episode)": "watch_media",  # Video context search
    r"\b(watch|view)\b": "watch_media",  # Video intent (default: cast device)
    r"\b(play|listen)\b": "play_media",          # Music intent (default: music assistant)
    # Volume Controls (Explicit before generic stop/play)
    r"\b(turn|set|change)\s+(the\s+)?volume\s+(to|at)\b": "volume_set", 
    r"\b(volume)\s+(set|to|at)\b": "volume_set",
    r"\b(turn|move)\s+(the\s+)?volume\s+up\b": "volume_up",
    r"\b(turn|move)\s+(the\s+)?volume\s+down\b": "volume_down",
    r"\bvolume\s+up\b": "volume_up",
    r"\bvolume\s+down\b": "volume_down",
    r"\b(mute|unmute|silence|quiet)\b": "volume_mute",
    r"\bturn\s+up\s+(the\s+)?volume\b": "volume_up",
    r"\bturn\s+down\s+(the\s+)?volume\b": "volume_down",
    
    r"\b(stop)\b": "stop_media",
    # Timer control patterns (specific matches before generic ones)
    r"\bpause\s+(the\s+)?(?:timer|alarm)\b": "timer_pause",
    r"\bresume\s+(the\s+)?(?:timer|alarm)\b": "timer_resume",
    r"\b(?:cancel|stop|delete|remove)\s+(the\s+)?(?:timer|alarm)\b": "timer_delete",

    r"\b(pause)\b": "pause_media",
    r"\b(resume|unpause)\b": "media_play",
    r"\b(skip|next)\b": "media_next",
    r"\b(previous|back|prev)\b": "media_previous",
    r"\b(scroll|move|go)\s+up\b": "nav_up",
    r"\b(scroll|move|go)\s+down\b": "nav_down",
    r"\b(scroll|move|go)\s+left\b": "nav_left",
    r"\b(scroll|move|go)\s+right\b": "nav_right",
    r"\bgo back\b|\bback\b": "nav_back",
    r"\b(go|back|return)\s+(to\s+)?(the\s+)?home\b": "nav_home",
    r"\bhome\s+screen\b": "nav_home",
    r"^home$": "nav_home",
    r"\bselect\b|\benter\b|\bok\b": "nav_enter",
    # Color control: matches "set/change/make X color" OR "turn X to color"
    r"\b(set|change|make).+(color|colour|red|blue|green|purple|orange|yellow|pink|white|warm|cool)": "set_color",
    r"\bturn\s+.+\s+(?:to\s+)?(red|blue|green|purple|orange|yellow|pink|white|warm|cool)": "set_color",
    r"\b(dim|darken|lower)\b": "dim",
    r"\b(brighten|brighter|increase)\b": "brighten",
    r"\b(brighten|brighter|increase)\b": "brighten",
    r"\b(brightness|bright)\b": "set_brightness",

    # Note Check-off (High Priority Deterministic Match)
    r"\b(check|tick|mark)\s+(off|done|complete)\b": "note_check_off",
    r"\b(check|mark)\s+.+\s+(off|done|complete)\b": "note_check_off",

    # Music Assistant Browsing
    r"\b(list|show|what)\s+(are|my)?\s*(playlists)": "list_playlists",
    r"\b(list|show|what)\s+(are|my)?\s*(radio|stations)": "list_radio",
}
