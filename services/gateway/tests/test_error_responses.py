"""Unhandled gateway errors must not leak internals to the client.

The old global handler returned ``detail`` (the exception string) and the full
formatted traceback in the 500 body. Exception text routinely contains
credentials and internal paths. The client now gets a generic message and an
error id; the traceback is logged server-side under that same id.
"""
import json
import logging

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from services.gateway import main as gateway_main


def _request(path: str = "/boom") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 12345),
            "root_path": "",
        }
    )


@pytest.mark.asyncio
async def test_the_global_handler_hides_the_exception_and_traceback(caplog):
    secret = "db-password-hunter2"
    with caplog.at_level(logging.ERROR, logger="gateway"):
        try:
            raise RuntimeError(f"cannot connect: {secret}")
        except RuntimeError as exc:
            resp = await gateway_main.global_exception_handler(_request(), exc)

    body = json.loads(resp.body)
    text = resp.body.decode()

    assert resp.status_code == 500
    # No exception text, no "detail", no traceback frames.
    assert secret not in text
    assert "traceback" not in body
    assert "detail" not in body
    assert "Traceback (most recent call last)" not in text
    # A stable id the caller can quote, in both the body and a header.
    assert body["error_id"]
    assert resp.headers["x-error-id"] == body["error_id"]
    # The server log still has the full diagnosis.
    assert secret in caplog.text
    assert "Traceback (most recent call last)" in caplog.text
    assert body["error_id"] in caplog.text


def test_the_middleware_500_is_generic_and_traceable(caplog):
    """The request middleware is the path most exceptions actually take."""
    secret = "upstream-token-abc123"
    path = "/__test_raise__"

    async def _boom():
        raise ValueError(f"bad upstream response: {secret}")

    gateway_main.app.add_api_route(path, _boom, methods=["GET"])
    try:
        with caplog.at_level(logging.ERROR, logger="gateway"):
            client = TestClient(gateway_main.app, raise_server_exceptions=False)
            resp = client.get(path)
    finally:
        gateway_main.app.router.routes[:] = [
            route
            for route in gateway_main.app.router.routes
            if getattr(route, "path", None) != path
        ]

    body = json.loads(resp.text)
    assert resp.status_code == 500
    assert secret not in resp.text
    assert "traceback" not in body
    assert "detail" not in body
    assert body["error_id"]
    assert resp.headers["x-error-id"] == body["error_id"]
    assert secret in caplog.text
    assert body["error_id"] in caplog.text
