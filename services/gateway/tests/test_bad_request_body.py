"""A request whose body is not JSON is the caller's fault: 400, not a 500."""
import json

from fastapi.testclient import TestClient

from services.gateway.main import _json_error_from_request_body, app


def test_a_body_that_is_not_json_is_a_400_not_a_500():
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/api/activity", content=b"", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["message"] == "The request body must be JSON"
    resp = client.post("/api/activity", content=b"{not json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400


def test_an_upstream_answer_that_is_not_json_is_still_the_gateways_fault():
    try:
        json.loads("<html>Bad Gateway</html>")
    except json.JSONDecodeError as e:
        assert _json_error_from_request_body(e) is False
