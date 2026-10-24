import asyncio

import aiohttp

from services.gateway import service_probes as sp


class _Resp:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Client:
    def __init__(self, outcomes):
        self.outcomes = outcomes

    def get(self, url, **kw):
        outcome = self.outcomes[url]
        if isinstance(outcome, Exception):
            raise outcome
        return _Resp(outcome)


def test_ok_on_expected_status_and_timing():
    result = asyncio.run(sp.probe_http(_Client({"http://svc/ping": 200}), "svc", "http://svc/ping"))
    assert result.ok and result.status == sp.OK
    assert result.code == 200 and result.line == "OK"
    assert result.milliseconds is not None and result.milliseconds >= 0
    assert result.as_dict()["ok"] is True


def test_unexpected_status_is_an_error_with_the_code():
    result = asyncio.run(sp.probe_http(_Client({"http://svc/ping": 502}), "svc", "http://svc/ping"))
    assert not result.ok and result.status == sp.ERROR
    assert result.code == 502 and result.line == "ERROR (502)"


def test_timeout_and_connection_failure_are_unreachable():
    timeout = asyncio.run(
        sp.probe_http(_Client({"http://svc": TimeoutError()}), "svc", "http://svc")
    )
    assert timeout.status == sp.UNREACHABLE
    assert "No answer within" in timeout.detail

    refused = asyncio.run(
        sp.probe_http(_Client({"http://svc": aiohttp.ClientConnectionError("refused")}), "svc", "http://svc")
    )
    assert refused.status == sp.UNREACHABLE
    assert "ClientConnectionError" in refused.detail


def test_unexpected_exception_is_an_error_not_a_crash():
    result = asyncio.run(sp.probe_http(_Client({"http://svc": ValueError("bad")}), "svc", "http://svc"))
    assert result.status == sp.ERROR and result.detail == "ValueError"


def test_missing_or_invalid_url_is_reported_not_guessed():
    missing = asyncio.run(sp.probe_http(_Client({}), "svc", None))
    assert missing.status == sp.UNAVAILABLE and missing.url == ""

    bad = asyncio.run(sp.probe_http(_Client({}), "svc", "svc.local/ping"))
    assert bad.status == sp.ERROR and "http://" in bad.detail


def test_expect_none_accepts_any_answer():
    result = asyncio.run(
        sp.probe_http(_Client({"http://svc": 403}), "svc", "http://svc/", expect=None)
    )
    assert result.ok and result.code == 403


def test_probe_many_preserves_order_and_per_target_expectations():
    client = _Client({"http://a": 200, "http://b": 503})
    results = asyncio.run(sp.probe_many(client, {"first": "http://a/", "second": "http://b/"},
                                       expectations={"second": None}))
    assert [r.name for r in results] == ["first", "second"]
    assert results[0].ok and results[1].ok
    assert results[1].status == sp.OK
