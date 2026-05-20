import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from announce_handlers import detect_tv_type

def test_detect_cast_by_entity_id():
    assert detect_tv_type("media_player.office_tv_chrome", "idle", {}) == "cast"
    assert detect_tv_type("media_player.living_room_cast", "idle", {}) == "cast"

def test_detect_cast_by_app_id():
    assert detect_tv_type("media_player.tv", "idle", {"app_id": "CC1AD845"}) == "cast"
    assert detect_tv_type("media_player.tv", "idle", {"app_id": "9ac10326"}) == "cast"

def test_detect_roku_by_entity_id():
    assert detect_tv_type("media_player.28_tcl_roku_tv", "idle", {}) == "roku"

def test_detect_roku_by_sources():
    assert detect_tv_type("media_player.tv", "idle", {
        "source_list": ["Home", "Netflix", "The Roku Channel", "Hulu"]
    }) == "roku"

def test_detect_android_tv():
    assert detect_tv_type("media_player.office_tv", "on", {
        "app_id": "com.google.android.tvlauncher"
    }) == "android_tv"
    assert detect_tv_type("media_player.tv", "idle", {
        "app_id": "com.google.android.apps.mediashell"
    }) == "android_tv"
    assert detect_tv_type("media_player.android_tv_remote", "on", {
        "device_class": "tv",
        "app_id": "com.google.android.backdrop"
    }) == "android_tv"

def test_detect_with_loaded_components():
    components = {"cast.media_player", "media_player", "roku"}
    # cast.media_player in components + SUPPORT_PLAY_MEDIA flag
    assert detect_tv_type("media_player.unknown", "idle", {"supported_features": 16384}, components) == "cast"
    # roku in components + SUPPORT_BROWSE_MEDIA flag
    assert detect_tv_type("media_player.unknown", "idle", {"supported_features": 131072}, components) == "roku"
