from gateway.main import resolve_media_target, resolve_video_target


def test_resolve_media_target_prefers_same_name_music_assistant_queue():
    entities = [
        {
            "entity_id": "media_player.office_tv_chrome_2",
            "state": "unavailable",
            "attributes": {"friendly_name": "Office TV", "device_class": "speaker"},
        },
        {
            "entity_id": "media_player.office_tv_3",
            "state": "playing",
            "attributes": {
                "friendly_name": "Office TV",
                "source": "Music Assistant Queue",
                "device_class": "speaker",
            },
        },
        {
            "entity_id": "media_player.office_speaker",
            "state": "idle",
            "attributes": {
                "friendly_name": "Office Speaker",
                "source": "Music Assistant Queue",
                "device_class": "speaker",
            },
        },
    ]

    target = resolve_media_target("Play Brandon Lake on Office TV", entities)

    assert target == "media_player.office_tv_3"


def test_resolve_media_target_matches_queue_when_requested_name_contains_suffix():
    entities = [
        {
            "entity_id": "media_player.office_tv_remote",
            "state": "on",
            "attributes": {"friendly_name": "Office TV Remote", "device_class": "tv"},
        },
        {
            "entity_id": "media_player.office_tv_3",
            "state": "idle",
            "attributes": {
                "friendly_name": "Office TV",
                "source": "Music Assistant Queue",
                "device_class": "speaker",
            },
        },
    ]

    target = resolve_media_target("Play jazz on the Office TV Remote", entities)

    assert target == "media_player.office_tv_3"


def test_resolve_media_target_rejects_nearby_non_matching_music_assistant_queue():
    entities = [
        {
            "entity_id": "media_player.office_speaker",
            "state": "playing",
            "attributes": {
                "friendly_name": "Office Speaker",
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

    target = resolve_media_target("Play worship music on Office TV", entities)

    assert target == "auto"


def test_resolve_video_target_prefers_cast_like_device_for_matching_tv():
    entities = [
        {
            "entity_id": "media_player.office_tv_queue",
            "state": "playing",
            "attributes": {
                "friendly_name": "Office TV",
                "source": "Music Assistant Queue",
                "device_class": "speaker",
            },
        },
        {
            "entity_id": "media_player.office_tv_cast",
            "state": "idle",
            "attributes": {
                "friendly_name": "Office TV Cast",
                "device_class": "tv",
            },
        },
    ]

    target = resolve_video_target("Play a music video on Office TV", entities)

    assert target == "media_player.office_tv_cast"
