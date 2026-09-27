"""BUG-06: URLs logged by the gateway must never contain secrets.

Adds unit coverage for ``redact_url()`` plus an end-to-end caplog test: a
request carrying ``token=SECRET`` in the query string must never appear in
any gateway log record (the secure_logging_middleware used to log
``request.url`` verbatim).
"""
import logging

from services.gateway.redact import redact_url
from services.gateway.tests.conftest_media import mock_upstream


def test_redact_url_masks_token_query_value():
    out = redact_url("http://ma.local:8095/ws?token=SECRET")
    assert "SECRET" not in out
    assert "token=***" in out


def test_redact_url_masks_api_key_and_access_token():
    out = redact_url("https://host/x?api_key=AK123&access_token=AT456&keep=1")
    assert "AK123" not in out
    assert "AT456" not in out
    assert "api_key=***" in out
    assert "access_token=***" in out
    assert "keep=1" in out


def test_redact_url_is_case_insensitive_on_keys():
    out = redact_url("http://h/x?Token=SECRET&API_KEY=AK")
    assert "SECRET" not in out
    assert "AK" not in out


def test_redact_url_leaves_benign_urls_unchanged():
    plain = "http://ma.local:8095/imageproxy?path=art.jpg"
    assert redact_url(plain) == plain
    assert redact_url("http://h/path") == "http://h/path"
    assert redact_url("") == ""


def test_imageproxy_request_never_logs_token(client, upstream, caplog):
    """End-to-end: token=SECRET in the query must never reach gateway logs."""
    mock_upstream(upstream, "GET", "http://ma.local:8095/imageproxy", payload="img")
    with caplog.at_level(logging.INFO):
        resp = client.get(
            "/api/media/imageproxy",
            params={
                "path": "http://ma.local:8095/imageproxy?path=art.jpg",
                "token": "SECRET-XYZ",
            },
        )
    assert resp.status_code == 200
    gateway_msgs = "\n".join(
        r.getMessage() for r in caplog.records if r.name.startswith("gateway")
    )
    assert "SECRET-XYZ" not in gateway_msgs
    assert "token=***" in gateway_msgs
