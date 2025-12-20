"""
Roku channel utilities for discovering and selecting appropriate playback channels
"""
import requests
import logging
import xml.etree.ElementTree as ET
from typing import Optional, Dict

log = logging.getLogger(__name__)

# Preferred channels for video playback, in order of preference
VIDEO_PLAYBACK_CHANNELS = [
    "Roku Media Player",
    "The Roku Channel", 
    "Play On Roku"
]

def get_roku_installed_apps(roku_ip: str) -> Dict[str, str]:
    """
    Query Roku for all installed apps/channels
    Returns dict mapping app_name -> app_id
    """
    try:
        resp = requests.get(f"http://{roku_ip}:8060/query/apps", timeout=3)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            apps = {}
            for app in root.findall('.//app'):
                app_id = app.get('id')
                app_name = app.text
                if app_id and app_name:
                    apps[app_name.strip()] = app_id
            log.info(f"[Roku] Found {len(apps)} installed apps")
            return apps
    except Exception as e:
        log.error(f"[Roku] Error getting installed apps: {e}")
    return {}

def find_video_playback_channel(roku_ip: str) -> Optional[str]:
    """
    Find the best channel for video playback on this Roku
    Returns channel ID or None
    """
    apps = get_roku_installed_apps(roku_ip)
    
    # Try preferred channels in order
    for channel_name in VIDEO_PLAYBACK_CHANNELS:
        if channel_name in apps:
            channel_id = apps[channel_name]
            log.info(f"[Roku] Selected '{channel_name}' (ID: {channel_id}) for video playback")
            return channel_id
    
    # Fallback: look for any app with "media" or "player" in name
    for app_name, app_id in apps.items():
        if any(keyword in app_name.lower() for keyword in ['media', 'player', 'video']):
            log.warning(f"[Roku] Using fallback channel '{app_name}' (ID: {app_id})")
            return app_id
    
    log.error("[Roku] Could not find suitable video playback channel")
    return None
