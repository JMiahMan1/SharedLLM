from services.gateway.main import resolve_media_target, resolve_video_target


def test_resolve_media_target_prefers_same_name_music_assistant_queue():
    entities = [
        {
            "entity_id": "media_player.study_tv_cast_2",
            "state": "unavailable",
            "attributes": {"friendly_name": "Study TV", "device_class": "speaker"},
        },
        {
            "entity_id": "media_player.den_tv",
            "state": "playing",
            "attributes": {
                "friendly_name": "Study TV",
                "source": "Music Assistant Queue",
                "device_class": "speaker",
            },
        },
        {
            "entity_id": "media_player.study_speaker",
            "state": "idle",
            "attributes": {
                "friendly_name": "Study Speaker",
                "source": "Music Assistant Queue",
                "device_class": "speaker",
            },
        },
    ]

    target = resolve_media_target("Play Brandon Lake on Study TV", entities)

    assert target == "media_player.den_tv"


def test_resolve_media_target_matches_queue_when_requested_name_contains_suffix():
    entities = [
        {
            "entity_id": "media_player.study_tv_remote",
            "state": "on",
            "attributes": {"friendly_name": "Study TV Remote", "device_class": "tv"},
        },
        {
            "entity_id": "media_player.den_tv",
            "state": "idle",
            "attributes": {
                "friendly_name": "Study TV",
                "source": "Music Assistant Queue",
                "device_class": "speaker",
            },
        },
    ]

    target = resolve_media_target("Play jazz on the Study TV Remote", entities)

    assert target == "media_player.den_tv"


def test_resolve_media_target_rejects_nearby_non_matching_music_assistant_queue():
    entities = [
        {
            "entity_id": "media_player.study_speaker",
            "state": "playing",
            "attributes": {
                "friendly_name": "Study Speaker",
                "source": "Music Assistant Queue",
                "device_class": "speaker",
            },
        },
        {
            "entity_id": "media_player.kitchen_speaker",
            "state": "idle",
            "attributes": {
                "friendly_name": "Kitchen Speaker",
                "source": "Music Assistant Queue",
                "device_class": "speaker",
            },
        },
    ]

    target = resolve_media_target("Play worship music on Study TV", entities)

    assert target is None


def test_resolve_video_target_prefers_cast_like_device_for_matching_tv():
    entities = [
        {
            "entity_id": "media_player.study_tv_queue",
            "state": "playing",
            "attributes": {
                "friendly_name": "Study TV",
                "source": "Music Assistant Queue",
                "device_class": "speaker",
            },
        },
        {
            "entity_id": "media_player.study_tv_cast",
            "state": "idle",
            "attributes": {
                "friendly_name": "Study TV Cast",
                "device_class": "tv",
            },
        },
    ]

    target = resolve_video_target("Play a music video on Study TV", entities)

    assert target == "media_player.study_tv_cast"
