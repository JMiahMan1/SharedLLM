"""Admins hear when a service goes down or comes back, and when the desktop
GPU services stop answering, once per change (services/gateway/admin_alerts.py)."""
import asyncio
import json

from services.gateway import admin_alerts


def test_service_is_down_only_after_two_failed_checks_in_a_row():
    state: dict = {}
    assert admin_alerts.service_transitions(state, {"rag": "UNREACHABLE"}) == []
    notices = admin_alerts.service_transitions(state, {"rag": "UNREACHABLE"})
    assert [n["title"] for n in notices] == ["Service down: rag"]
    assert notices[0]["tag"] == "admin:service:rag"
    assert "UNREACHABLE" in notices[0]["body"]
    assert notices[0]["actions"] == [{"label": "Open", "type": "view", "url": "/"}]
    # Still down: no second notice.
    assert admin_alerts.service_transitions(state, {"rag": "UNREACHABLE"}) == []


def test_one_failed_check_between_good_ones_is_not_an_outage():
    state: dict = {}
    for status in ("ERROR (502)", "OK", "ERROR (502)", "OK"):
        assert admin_alerts.service_transitions(state, {"geo": status}) == []


def test_back_up_replaces_down_with_the_same_tag():
    state: dict = {}
    admin_alerts.service_transitions(state, {"workspace_runtime": "UNREACHABLE"})
    down = admin_alerts.service_transitions(state, {"workspace_runtime": "UNREACHABLE"})
    up = admin_alerts.service_transitions(state, {"workspace_runtime": "OK"})
    assert [n["title"] for n in up] == ["Service back up: workspace runtime"]
    assert up[0]["tag"] == down[0]["tag"] == "admin:service:workspace_runtime"
    assert admin_alerts.service_transitions(state, {"workspace_runtime": "OK"}) == []


def test_desktop_alert_waits_for_the_configured_minutes():
    state: dict = {}
    t0 = 1_000_000.0
    assert admin_alerts.desktop_transitions(state, {"Audio server": False}, t0, 10) == []
    assert admin_alerts.desktop_transitions(state, {"Audio server": False}, t0 + 540, 10) == []
    notices = admin_alerts.desktop_transitions(state, {"Audio server": False}, t0 + 600, 10)
    assert [n["title"] for n in notices] == ["Desktop GPU service unreachable"]
    assert "Audio server" in notices[0]["body"] and notices[0]["tag"] == "admin:desktop"
    assert admin_alerts.desktop_transitions(state, {"Audio server": False}, t0 + 660, 10) == []
    back = admin_alerts.desktop_transitions(state, {"Audio server": True}, t0 + 720, 10)
    assert [n["title"] for n in back] == ["Desktop GPU services back"]
    assert back[0]["tag"] == "admin:desktop"


def test_a_short_blip_on_the_desktop_says_nothing():
    state: dict = {}
    t0 = 1_000_000.0
    admin_alerts.desktop_transitions(state, {"LLM proxy": False}, t0, 10)
    assert admin_alerts.desktop_transitions(state, {"LLM proxy": True}, t0 + 300, 10) == []
    # The clock restarts after an answer.
    assert admin_alerts.desktop_transitions(state, {"LLM proxy": False}, t0 + 360, 10) == []
    assert admin_alerts.desktop_transitions(state, {"LLM proxy": False}, t0 + 900, 10) == []


def test_desktop_notice_follows_the_set_of_unreachable_services():
    state: dict = {}
    t0 = 1_000_000.0
    both = {"LLM proxy": False, "Audio server": False}
    admin_alerts.desktop_transitions(state, both, t0, 10)
    first = admin_alerts.desktop_transitions(state, both, t0 + 600, 10)
    assert len(first) == 1 and "Audio server, LLM proxy" in first[0]["body"]
    partial = admin_alerts.desktop_transitions(state, {"LLM proxy": True, "Audio server": False}, t0 + 660, 10)
    assert partial[0]["title"] == "Desktop GPU service unreachable"
    assert "Back: LLM proxy" in partial[0]["body"]


def test_desktop_targets_skip_unset_and_duplicate_urls(monkeypatch):
    monkeypatch.setattr(admin_alerts, "ALPACA_AUDIO_URL", "")
    monkeypatch.setattr(admin_alerts, "ALPACA_WEB_URL", "http://desk:5000")
    monkeypatch.setattr(admin_alerts, "ALPACA_ARCADE_URL", "http://desk:5000/")
    targets = admin_alerts.desktop_targets({"llm_local_url": "http://desk:11434/", "vision_ocr_proxy_url": None})
    assert targets == {"LLM proxy": "http://desk:11434", "Alpaca dashboard": "http://desk:5000"}


class _Redis:
    def __init__(self):
        self.kv: dict[str, str] = {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value):
        self.kv[key] = value


class _Resp:
    def __init__(self, status, body=None):
        self.status = status
        self._body = body

    async def json(self, content_type=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Client:
    def __init__(self, services, telemetry_ok=True):
        self.services = services
        self.telemetry_ok = telemetry_ok
        self.notified: list[dict] = []

    def get(self, url, **kw):
        if url.endswith("/health/ready"):
            return _Resp(200, {"status": "DEGRADED", "services": {"gateway": "OK", **self.services}})
        raise ConnectionError(url)

    def post(self, url, json=None, **kw):
        assert url.endswith("/api/telemetry/notify")
        if not self.telemetry_ok:
            return _Resp(503)
        self.notified.append(json)
        return _Resp(200, {"status": "SUCCESS"})


def _run(r, client, now=1_000_000.0):
    return asyncio.run(admin_alerts.check_once(r, client, {}, gateway="http://gw", now=now))


def test_check_once_sends_to_admins_and_keeps_state_in_redis():
    r = _Redis()
    client = _Client({"rag": "UNREACHABLE", "geo": "OK"})
    assert _run(r, client) == 0
    assert _run(r, client) == 1
    notice = client.notified[0]
    assert notice["user"] == "@admins" and notice["category"] == "admin"
    assert notice["title"] == "Service down: rag"
    # A fresh worker (gateway restart) still knows rag was reported down.
    client.services["rag"] = "OK"
    assert _run(r, _Client(client.services)) == 1
    assert json.loads(r.kv[admin_alerts.STATE_KEY])["services"]["down"] == []


def test_a_notice_telemetry_refused_is_sent_on_the_next_pass():
    r = _Redis()
    down = _Client({"rag": "UNREACHABLE"}, telemetry_ok=False)
    _run(r, down)
    _run(r, down)
    assert json.loads(r.kv[admin_alerts.STATE_KEY])["outbox"][0]["title"] == "Service down: rag"
    up = _Client({"rag": "UNREACHABLE"})
    assert _run(r, up) == 1
    assert [n["title"] for n in up.notified] == ["Service down: rag"]
    assert json.loads(r.kv[admin_alerts.STATE_KEY])["outbox"] == []


def test_a_newer_notice_replaces_an_undelivered_one_with_its_tag():
    r = _Redis()
    client = _Client({"rag": "UNREACHABLE"}, telemetry_ok=False)
    _run(r, client)
    _run(r, client)
    client.services["rag"] = "OK"
    client.telemetry_ok = True
    _run(r, client)
    assert [n["title"] for n in client.notified] == ["Service back up: rag"]


def test_media_targets_build_the_right_liveness_probes():
    targets = admin_alerts.media_targets({
        "audiobookshelf_url": "https://abs.sumemail.com/",
        "mass_url": "http://ha.sumemail.com:8095/",
        "nextcloud_url": "https://cloud.sumemail.com/",
    })
    assert targets == {
        "audiobookshelf": "https://abs.sumemail.com/ping",
        "music_assistant": "http://ha.sumemail.com:8095",
        "nextcloud": "https://cloud.sumemail.com/status.php",
    }


def test_media_targets_accept_legacy_keys_and_skip_unset():
    assert admin_alerts.media_targets({"abs_url": "http://abs.local"}) == {
        "audiobookshelf": "http://abs.local/ping",
    }
    assert admin_alerts.media_targets({"ma_url": "  "}) == {}
    assert admin_alerts.media_targets({"nextcloud_url": "https://cloud.local/"}) == {
        "nextcloud": "https://cloud.local/status.php",
    }


class _MediaClient(_Client):
    """Readiness plus the backends' liveness URLs, with switchable failures."""

    def __init__(self, services, *, abs_down=False, ma_down=False, nc_down=False, telemetry_ok=True):
        super().__init__(services, telemetry_ok=telemetry_ok)
        self.abs_down = abs_down
        self.ma_down = ma_down
        self.nc_down = nc_down

    def get(self, url, **kw):
        if url.endswith("/health/ready"):
            return super().get(url, **kw)
        if url == "https://abs.local/ping":
            if self.abs_down:
                raise TimeoutError()
            return _Resp(200)
        if url == "http://ma.local":
            if self.ma_down:
                raise ConnectionError("refused")
            return _Resp(200)
        if url == "https://cloud.local/status.php":
            if self.nc_down:
                raise TimeoutError()
            return _Resp(200)
        raise ConnectionError(url)


_MEDIA_SETTINGS = {
    "audiobookshelf_url": "https://abs.local",
    "mass_url": "http://ma.local",
    "nextcloud_url": "https://cloud.local",
}


def _run_media(r, client, now=1_000_000.0):
    return asyncio.run(admin_alerts.check_once(r, client, _MEDIA_SETTINGS, gateway="http://gw", now=now))


def test_check_once_reports_a_wedged_audiobookshelf_after_two_failures():
    r = _Redis()
    assert _run_media(r, _MediaClient({"geo": "OK"})) == 0
    down = _MediaClient({"geo": "OK"}, abs_down=True)
    assert _run_media(r, down) == 0
    assert _run_media(r, down) == 1
    notice = down.notified[0]
    assert notice["title"] == "Service down: audiobookshelf"
    assert "UNREACHABLE" in notice["body"]
    assert notice["tag"] == "admin:service:audiobookshelf"


def test_check_once_reports_music_assistant_connection_refused():
    r = _Redis()
    client = _MediaClient({"geo": "OK"}, ma_down=True, telemetry_ok=False)
    _run_media(r, client)
    _run_media(r, client)
    queued = json.loads(r.kv[admin_alerts.STATE_KEY])["outbox"][0]
    assert queued["title"] == "Service down: music assistant"
    assert "UNREACHABLE" in queued["body"]


def test_check_once_reports_a_wedged_nextcloud():
    r = _Redis()
    client = _MediaClient({"geo": "OK"}, nc_down=True, telemetry_ok=False)
    _run_media(r, client)
    _run_media(r, client)
    queued = json.loads(r.kv[admin_alerts.STATE_KEY])["outbox"][0]
    assert queued["title"] == "Service down: nextcloud"
    assert "UNREACHABLE" in queued["body"]


def test_check_once_announces_media_services_back():
    r = _Redis()
    down = _MediaClient({"geo": "OK"}, abs_down=True)
    _run_media(r, down)
    _run_media(r, down)
    assert [n["title"] for n in down.notified] == ["Service down: audiobookshelf"]
    up = _MediaClient({"geo": "OK"})
    assert _run_media(r, up) == 1
    assert [n["title"] for n in up.notified] == ["Service back up: audiobookshelf"]
