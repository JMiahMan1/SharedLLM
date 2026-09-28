"""P2-T9 / BUG-18: MA browse via HA must unwrap service_response, pass
config_entry_id, and use lowercase singular media types.

Verified against homeassistant/components/music_assistant on HA core dev
(2026-09-27):
- services.yaml get_library: config_entry_id required: true; media_type
  select options = artist | album | audiobook | playlist | podcast | track | radio
  (lowercase singular — "TRACKS" is invalid).
- services.py handle_get_library returns LIBRARY_RESULTS_SCHEMA
  {"items": [...], "limit", "offset", "order_by", "media_type"} which HA wraps
  as {"changed_states": [...], "service_response": {...}} — top-level keys are
  never "tracks"/"playlists"/etc.
"""
import os


from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Recorded HA response (handle_get_library -> LIBRARY_RESULTS_SCHEMA, inside the
# HA service-API wrapper). Items shaped like media_item_dict_from_mass_item.
RECORDED_HA_RESPONSE = {
    "changed_states": [],
    "service_response": {
        "items": [
            {
                "media_type": "track",
                "uri": "library://track/abc123",
                "name": "Bohemian Rhapsody",
                "version": "",
                "image": "http://ma.local:8095/imageproxy/x",
                "duration": 354.0,
            }
        ],
        "limit": 50,
        "offset": 0,
        "order_by": "name",
        "media_type": "track",
    },
}


@pytest.mark.asyncio
async def test_get_library_unwraps_service_response_and_normalizes_type():
    from services.execution.handlers import mass_ha_client

    mock_call = AsyncMock(return_value=RECORDED_HA_RESPONSE)
    with patch("services.execution.handlers.mass_ha_client._call_ha_ma_service", mock_call):
        items = await mass_ha_client.get_library(
            "http://ha.local:8123",
            "test-ha-token",
            "TRACKS",
            limit=50,
            offset=0,
            mass_entry_id="entry-1",
        )

    # Exactly one upstream call, with the enum the HA integration accepts and
    # the config_entry_id its service schema requires.
    assert mock_call.await_count == 1
    _ha_url, _ha_token, service, service_data = mock_call.call_args[0]
    assert service == "get_library"
    assert service_data["media_type"] == "track"
    assert service_data["config_entry_id"] == "entry-1"
    assert service_data["limit"] == 50
    assert service_data["offset"] == 0

    # service_response unwrapped: items actually returned (was always [] before).
    assert len(items) == 1
    assert items[0]["name"] == "Bohemian Rhapsody"
    assert items[0]["uri"] == "library://track/abc123"
    assert items[0]["type"] == "track"
    assert items[0]["duration"] == 354.0


@pytest.mark.asyncio
async def test_get_library_omits_config_entry_id_when_unset():
    """No entry id → field omitted (not sent as empty); type still normalized."""
    from services.execution.handlers import mass_ha_client

    mock_call = AsyncMock(
        return_value={"changed_states": [], "service_response": {"items": []}}
    )
    with patch("services.execution.handlers.mass_ha_client._call_ha_ma_service", mock_call):
        items = await mass_ha_client.get_library(
            "http://ha.local:8123", "test-ha-token", "PLAYLISTS", mass_entry_id=""
        )

    service_data = mock_call.call_args[0][3]
    assert service_data["media_type"] == "playlist"
    assert "config_entry_id" not in service_data
    assert items == []


def test_browse_endpoint_passes_mass_config_entry_id():
    from services.execution.main import app

    client = TestClient(app)
    mock_creds = {
        "ha_url": "http://ha.local:8123",
        "ha_token": "test-ha-token",
        "mass_config_entry_id": "entry-9",
    }
    with patch(
        "services.execution.main._resolve_mass_ha_creds",
        new=AsyncMock(return_value=mock_creds),
    ):
        mock_call = AsyncMock(return_value=RECORDED_HA_RESPONSE)
        with patch(
            "services.execution.handlers.mass_ha_client._call_ha_ma_service", mock_call
        ):
            resp = client.get(
                "/execute/media/music-assistant/browse?media_type=TRACKS",
                headers={"X-Internal-Secret": os.environ["INTERNAL_SECRET"]},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "SUCCESS"
    assert len(body["items"]) == 1
    assert body["items"][0]["type"] == "track"

    service_data = mock_call.call_args[0][3]
    assert service_data["media_type"] == "track"
    assert service_data["config_entry_id"] == "entry-9"
