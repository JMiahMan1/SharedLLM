import os
import time
import httpx
import pytest
from dotenv import load_dotenv

load_dotenv()

if os.getenv("CI") or os.getenv("GITHUB_ACTIONS"):
    pytest.skip("Skipping local Office TV media control test in CI.", allow_module_level=True)

HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
GATEWAY_URL = "http://localhost:11435"
QUEUE_ENTITY_ID = "media_player.office_tv_3"

HA_HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}


@pytest.fixture
def client():
    return httpx.Client(timeout=60.0)


def get_ha_state(entity_id: str):
    with httpx.Client(verify=False, timeout=15.0) as client:
        resp = client.get(f"{HA_URL}/api/states/{entity_id}", headers=HA_HEADERS)
        if resp.status_code == 200:
            return resp.json()
        return None


def summarize_state(entity_id: str) -> dict:
    data = get_ha_state(entity_id)
    assert data is not None, f"Entity {entity_id} not found in HA"
    attrs = data.get("attributes", {})
    return {
        "state": data.get("state"),
        "media_title": attrs.get("media_title"),
        "media_artist": attrs.get("media_artist"),
        "friendly_name": attrs.get("friendly_name"),
        "source": attrs.get("source"),
    }


def send_chat(client: httpx.Client, query: str) -> dict:
    resp = client.post(f"{GATEWAY_URL}/api/chat", json={"query": query, "voice_id": "admin"})
    assert resp.status_code == 200, f"{query} failed: {resp.text}"
    return resp.json()


def wait_for(predicate, timeout: int = 20, interval: float = 2.0):
    deadline = time.time() + timeout
    last_value = None
    while time.time() < deadline:
        last_value = predicate()
        if last_value:
            return last_value
        time.sleep(interval)
    return last_value


def test_office_tv_music_controls(client):
    """
    Local-only hardware test:
    verify play, pause, next, back, and stop on the Office TV Music Assistant queue.
    """
    initial = summarize_state(QUEUE_ENTITY_ID)
    print(f"\n[Test] Initial Office TV state: {initial}")

    send_chat(client, "Play Brandon Lake on Office TV")
    play_state = wait_for(
        lambda: (
            state if (state := summarize_state(QUEUE_ENTITY_ID)).get("state") in {"playing", "buffering"}
            and state.get("media_artist") == "Brandon Lake" else None
        )
    )
    assert play_state, "Office TV did not start Brandon Lake playback."
    print(f"[Test] After play: {play_state}")

    send_chat(client, "Pause the Office TV")
    pause_state = wait_for(
        lambda: (
            state if (state := summarize_state(QUEUE_ENTITY_ID)).get("state") == "paused" else None
        ),
        timeout=10,
        interval=1.0,
    )
    assert pause_state, "Office TV did not pause."
    print(f"[Test] After pause: {pause_state}")

    previous_title = pause_state.get("media_title")
    send_chat(client, "Next on Office TV")
    next_state = wait_for(
        lambda: (
            state if (state := summarize_state(QUEUE_ENTITY_ID)).get("state") == "playing"
            and state.get("media_title") != previous_title else None
        )
    )
    assert next_state, "Office TV did not advance to a new track."
    print(f"[Test] After next: {next_state}")

    next_title = next_state.get("media_title")
    send_chat(client, "Back on Office TV")
    back_state = wait_for(
        lambda: (
            state if (state := summarize_state(QUEUE_ENTITY_ID)).get("state") == "playing"
            and state.get("media_title") != next_title else None
        ),
        timeout=8,
        interval=1.0,
    )
    if not back_state:
        send_chat(client, "Back on Office TV")
        send_chat(client, "Back on Office TV")
        back_state = wait_for(
            lambda: (
                state if (state := summarize_state(QUEUE_ENTITY_ID)).get("state") == "playing"
                and state.get("media_title") != next_title else None
            ),
            timeout=8,
            interval=1.0,
        )
    assert back_state, "Office TV back command did not move away from the current track."
    print(f"[Test] After back: {back_state}")

    send_chat(client, "Stop the music on Office TV")
    stop_state = wait_for(
        lambda: (
            state if (state := summarize_state(QUEUE_ENTITY_ID)).get("state") == "idle" else None
        ),
        timeout=10,
        interval=1.0,
    )
    assert stop_state, "Office TV did not stop playback."
    print(f"[Test] After stop: {stop_state}")


if __name__ == "__main__":
    pytest.main([__file__, "-s"])
