"""What reaches the dashboard's "Recent problems": real faults, not bots
probing the server, routine notices, or a service restarting for a deploy."""
import aiohttp
from fastapi.testclient import TestClient

from services.gateway import main
from services.gateway.main import _response_log_level, app


def test_a_bot_probing_paths_the_app_never_uses_is_not_a_problem():
    assert _response_log_level(404, "/") == "INFO"
    assert _response_log_level(404, "/wp-login.php") == "INFO"


def test_a_missing_api_route_and_other_refusals_still_are():
    assert _response_log_level(404, "/api/notes/42") == "WARNING"
    assert _response_log_level(401, "/") == "WARNING"
    assert _response_log_level(500, "/") == "ERROR"
    assert _response_log_level(200, "/api/notes") == "INFO"


def test_a_service_restarting_is_a_503_to_retry_not_a_500(monkeypatch):
    async def down(request):
        raise aiohttp.ServerDisconnectedError()

    monkeypatch.setattr(main, "_acting_identity", down)
    logged = []

    async def emit(level, message, context):
        logged.append((level, message))

    monkeypatch.setattr(main, "emit_log", emit)
    resp = TestClient(app, raise_server_exceptions=False).get("/api/geo/zones")
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "5"
    assert not any(level == "ERROR" for level, _ in logged)


def test_a_bot_guessing_api_paths_is_not_a_problem_but_the_app_missing_one_is(monkeypatch):
    logged = []

    async def emit(level, message, context):
        logged.append((level, message))

    monkeypatch.setattr(main, "emit_log", emit)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/api/v1/foundation-models").status_code == 404
    assert client.get("/api/old-route", headers={"Authorization": "Bearer k"}).status_code == 404
    levels = {message.split()[2]: level for level, message in logged if "RESPONSE" in message}
    assert levels["/api/v1/foundation-models"] == "INFO"
    assert levels["/api/old-route"] == "WARNING"
