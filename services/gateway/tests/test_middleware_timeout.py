"""A timeout burst must not fill the log with full tracebacks.

The middleware logs one line for TimeoutError (a slow dependency, or a
client that gave up mid-flight) and keeps exc_info for real faults, so a
single slow minute no longer produces hundreds of ERROR tracebacks.
"""

import json
import logging
from unittest.mock import AsyncMock

from starlette.requests import Request

from services.gateway import main as gateway_main


def _request(path: str = "/api/geo/steps") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer secret")],
        "server": ("test", 80),
        "scheme": "http",
        "client": ("127.0.0.1", 1234),
    }
    return Request(scope)


async def test_timeout_gets_one_line_without_traceback(monkeypatch, caplog):
    monkeypatch.setattr(gateway_main, "emit_log", AsyncMock())

    async def boom(request):
        raise TimeoutError()

    with caplog.at_level(logging.ERROR, logger="gateway"):
        resp = await gateway_main.secure_logging_middleware(_request(), boom)

    assert resp.status_code == 500
    assert json.loads(resp.body)["error_id"]
    assert resp.headers["x-error-id"]
    timeouts = [r for r in caplog.records if "Middleware timeout" in r.getMessage()]
    errors = [r for r in caplog.records if "Middleware error" in r.getMessage()]
    assert len(timeouts) == 1
    assert errors == []
    assert timeouts[0].exc_info is None


async def test_a_real_fault_keeps_its_traceback(monkeypatch, caplog):
    monkeypatch.setattr(gateway_main, "emit_log", AsyncMock())

    async def boom(request):
        raise ValueError("nope")

    with caplog.at_level(logging.ERROR, logger="gateway"):
        resp = await gateway_main.secure_logging_middleware(_request(), boom)

    assert resp.status_code == 500
    faults = [r for r in caplog.records if "Middleware error" in r.getMessage()]
    assert len(faults) == 1
    assert faults[0].exc_info is not None
