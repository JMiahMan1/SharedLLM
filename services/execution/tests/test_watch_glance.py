"""A paired watch's glance line from family members' drive time home."""
import time

import pytest

from services.execution import watch_glance


def test_the_line_is_short_and_readable():
    etas = [("michele", {"to": "Home", "duration_s": 1080}), ("kate", {"to": "Home", "duration_s": 4500})]
    assert watch_glance.glance_line(etas) == "Michele · home 18m  Kate · home 1h15"
    assert watch_glance.glance_line([("michele", {"arrived": True})]) == ""
    assert len(watch_glance.glance_line([("x" * 80, {"duration_s": 60})])) <= watch_glance.MAX_LEN


class _Session:
    def __init__(self, locations, eta_status=200):
        self.locations, self.eta_status, self.asked = locations, eta_status, []

    def request(self, method, url, **kw):
        session = self

        class Resp:
            async def __aenter__(self):
                if url.endswith("/location/all"):
                    self.status, self.body = 200, session.locations
                else:
                    session.asked.append((url, kw.get("params")))
                    self.status, self.body = session.eta_status, {"to": "Home", "duration_s": 600}
                return self

            async def __aexit__(self, *a):
                pass

            async def json(self, content_type=None):
                return self.body
        return Resp()


@pytest.mark.asyncio
async def test_only_others_on_the_move_are_asked_about_as_the_owner():
    now = time.time()
    session = _Session({
        "jeremiah": {"speed": 25, "updated_at": now},            # the owner: never
        "michele": {"speed": 20, "updated_at": now},             # driving: yes
        "kate": {"speed": 0, "updated_at": now},                 # still: no
        "default": {"speed": 30, "updated_at": now - 3600},      # stale: no
    })
    line = await watch_glance.line_for("jeremiah", session)
    assert line == "Michele · home 10m"
    assert len(session.asked) == 1
    url, params = session.asked[0]
    assert url.endswith("/people/michele/eta") and params["viewer"] == "jeremiah"


@pytest.mark.asyncio
async def test_someone_not_sharing_with_the_owner_stays_off_the_watch():
    session = _Session({"michele": {"speed": 20, "updated_at": time.time()}}, eta_status=404)
    assert await watch_glance.line_for("jeremiah", session) == ""
