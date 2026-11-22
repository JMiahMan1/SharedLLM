"""The online fallback: default-deny, one typed payload, only while the local AI is offline.

What may leave the house is pinned here: the allowlist only takes features with
a template, the client only takes that feature's own payload type, the route
refuses any body with another field, the outbound provider request holds the
passage, question and reading level and nothing personal, and chat never
reaches it even with the breaker open and every feature allowlisted. Names
and hosts are fake.
"""
from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.gateway import cloud_fallback as cf
from services.gateway import local_ai

SECRET = "test-secret"
PROVIDER = cf.CloudProvider(url="https://openrouter.ai/api/v1/chat/completions", api_key="k",
                            model="some/model:free", name="openrouter")
BODY = {
    "feature": "bible_study_ask",
    "reference": "John 3:16",
    "verses": [{"reference": "John 3:16", "text": "For God so loved the world."}],
    "translation": "kjv",
    "question": "What does world mean here?",
    "reading_level": "adult",
}
PERSONAL = {
    "user": "alex-secret", "username": "alex-secret", "notes": ["NOTE-SECRET"], "highlights": ["HL-SECRET"],
    "history": [{"role": "user", "content": "HISTORY-SECRET"}], "context": "RAG-SECRET", "memory": "MEM-SECRET",
    "household": "HOUSE-SECRET",
}


def _request(**over) -> cf.BibleStudyRequest:
    fields = {"reference": "John 3:16", "verses": (cf.PublicVerse("John 3:16", "For God so loved the world."),),
              "translation": "kjv", "question": "Why?", "reading_level": "adult", **over}
    return cf.BibleStudyRequest(**fields)


@pytest.fixture
def sends(monkeypatch):
    """Every request that would leave the house, recorded instead of sent."""
    sent: list[tuple[cf.CloudProvider, list[dict]]] = []

    async def fake_send(provider, messages):
        sent.append((provider, messages))
        return "An answer naming Genesis 1:1."

    monkeypatch.setattr(cf, "_send", fake_send)
    return sent


@pytest.fixture
def down():
    """The local AI's breaker is open."""
    local_ai.LLM.set_url("http://gpu-box.test:11434")
    local_ai.LLM.mark_down("ClientConnectorError: refused")
    yield
    local_ai.LLM.reset()
    local_ai.take_waiting()


@pytest.fixture
def wide_open(monkeypatch):
    """The client as wide as it can be configured: every feature, a provider, breaker read live."""
    client = cf.CloudFallbackClient(frozenset(cf.FEATURES), PROVIDER, local_ai.LLM.is_down)
    monkeypatch.setattr(cf, "CLIENT", client)
    return client


# ------------------------------------------------------------------ allowlist


def test_the_default_allowlist_is_only_the_bible_question():
    assert cf.parse_allowlist(None) == frozenset({"bible_study_ask"})


@pytest.mark.parametrize("raw", ["", "  ", ","])
def test_an_empty_setting_turns_it_off(raw):
    assert cf.parse_allowlist(raw) == frozenset()


def test_names_are_trimmed_and_case_folded():
    assert cf.parse_allowlist(" Bible_Study_Ask , ") == frozenset({"bible_study_ask"})


@pytest.mark.parametrize("name", ["guardian", "chat", "raven", "app_vetting", "unlock", "verse_of_day", "bible"])
def test_a_feature_without_a_template_stops_startup_with_its_name(name):
    with pytest.raises(cf.CloudFallbackConfigError) as err:
        cf.parse_allowlist(f"bible_study_ask,{name}")
    assert repr(name) in str(err.value) and "bible_study_ask" in str(err.value)


def test_configure_raises_on_an_unknown_feature():
    with pytest.raises(cf.CloudFallbackConfigError):
        cf.configure({"cloud_fallback_features": "chat"})


def test_configure_reads_the_existing_provider_settings_and_the_model(monkeypatch):
    monkeypatch.setattr(cf, "CLIENT", cf.CLIENT)  # restored after the test
    client = cf.configure({"llm_cloud_url": "https://openrouter.ai/api/v1/chat/completions",
                           "llm_cloud_api_key": "k", "cloud_fallback_model": "m:free"})
    assert client.allowlist == frozenset({"bible_study_ask"})
    assert client.provider == cf.CloudProvider("https://openrouter.ai/api/v1/chat/completions", "k", "m:free", "openrouter")


@pytest.mark.parametrize("settings", [
    {"llm_cloud_api_key": "k", "cloud_fallback_model": ""},
    {"llm_cloud_api_key": "", "cloud_fallback_model": "m"},
    {"llm_cloud_url": "", "llm_cloud_api_key": "k", "cloud_fallback_model": "m"},
])
def test_a_missing_key_url_or_model_means_no_provider(settings):
    base = {"llm_cloud_url": "https://openrouter.ai/api/v1/chat/completions"}
    assert cf.provider_from_settings({**base, **settings}) is None


def test_the_client_is_off_until_configured():
    fresh = cf.CloudFallbackClient(frozenset(), None, lambda: True)
    assert fresh.allowlist == frozenset() and fresh.provider is None


# ------------------------------------------------------------------ the client


async def test_an_allowlisted_payload_goes_out_while_down(sends):
    client = cf.CloudFallbackClient(frozenset({"bible_study_ask"}), PROVIDER, lambda: True)
    assert await client.answer("bible_study_ask", _request()) == "An answer naming Genesis 1:1."
    assert len(sends) == 1


@pytest.mark.parametrize("feature,payload,reason", [
    ("chat", "anything", "not_allowed"),
    ("guardian", {"image_b64": "x"}, "not_allowed"),
    ("bible_study_ask", {**BODY}, "payload_type"),
    ("bible_study_ask", "John 3:16 Why?", "payload_type"),
])
async def test_anything_else_is_refused_and_nothing_is_sent(sends, feature, payload, reason):
    client = cf.CloudFallbackClient(frozenset(cf.FEATURES), PROVIDER, lambda: True)
    with pytest.raises(cf.CloudFallbackRefused) as err:
        await client.answer(feature, payload)
    assert err.value.reason == reason
    assert sends == []


async def test_a_subclass_of_the_payload_is_refused(sends):
    class Wider(cf.BibleStudyRequest):
        pass

    client = cf.CloudFallbackClient(frozenset(cf.FEATURES), PROVIDER, lambda: True)
    wider = Wider(**{f: getattr(_request(), f) for f in ("reference", "verses", "translation", "question",
                                                         "reading_level")})
    with pytest.raises(cf.CloudFallbackRefused):
        await client.answer("bible_study_ask", wider)
    assert sends == []


async def test_a_feature_not_on_the_allowlist_is_refused(sends):
    client = cf.CloudFallbackClient(frozenset(), PROVIDER, lambda: True)
    with pytest.raises(cf.CloudFallbackRefused) as err:
        await client.answer("bible_study_ask", _request())
    assert err.value.reason == "not_allowed" and sends == []


async def test_nothing_goes_online_while_the_local_ai_is_up(sends):
    client = cf.CloudFallbackClient(frozenset(cf.FEATURES), PROVIDER, lambda: False)
    with pytest.raises(cf.CloudFallbackRefused) as err:
        await client.answer("bible_study_ask", _request())
    assert err.value.reason == "local_up" and sends == []


async def test_no_provider_is_refused(sends):
    client = cf.CloudFallbackClient(frozenset(cf.FEATURES), None, lambda: True)
    with pytest.raises(cf.CloudFallbackRefused) as err:
        await client.answer("bible_study_ask", _request())
    assert err.value.reason == "no_provider" and sends == []


async def test_a_refusal_is_logged_once(sends, caplog):
    client = cf.CloudFallbackClient(frozenset(), PROVIDER, lambda: True)
    caplog.set_level(logging.WARNING, logger="gateway.cloud_fallback")
    for _ in range(3):
        with pytest.raises(cf.CloudFallbackRefused):
            await client.answer("chat", "x")
    assert len([r for r in caplog.records if "refused" in r.getMessage()]) == 1


async def test_an_answer_is_logged_at_info_with_the_feature_name_only(sends, caplog):
    client = cf.CloudFallbackClient(frozenset(cf.FEATURES), PROVIDER, lambda: True)
    caplog.set_level(logging.INFO, logger="gateway.cloud_fallback")
    await client.answer("bible_study_ask", _request(question="A private worry about my son?"))
    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert lines == ["[cloud-fallback] answered online: feature=bible_study_ask"]


@pytest.mark.parametrize("bad", [
    {"reading_level": "toddler"}, {"question": ""}, {"verses": ()}, {"translation": "kjv; drop"},
    {"verses": ({"reference": "x", "text": "y"},)},
])
def test_the_payload_type_checks_its_own_fields(bad):
    with pytest.raises(ValueError):
        _request(**bad)


# ------------------------------------------------------------------ what goes out


def test_the_rendered_messages_carry_only_the_payload():
    messages = cf.FEATURES["bible_study_ask"].render(_request(question="What does world mean here?"))
    text = json.dumps(messages)
    assert "John 3:16" in text and "For God so loved the world." in text and "KJV" in text
    assert "What does world mean here?" in text
    assert [m["role"] for m in messages] == ["system", "user"]


async def test_the_outbound_http_request_has_no_personal_fields(monkeypatch):
    """The real adapter's request body, captured at the HTTP client."""
    from services.gateway import main as gateway_main

    captured: list[dict] = []

    class _Resp:
        status = 200

        def raise_for_status(self):
            return None

        async def json(self):
            return {"choices": [{"message": {"content": "An answer."}}]}

    class _Client:
        async def post(self, url, json=None, headers=None, timeout=None):
            captured.append({"url": url, "json": json, "headers": headers})
            return _Resp()

    class _Ctx:
        async def __aenter__(self):
            return _Client()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: _Ctx())
    client = cf.CloudFallbackClient(frozenset(cf.FEATURES), PROVIDER, lambda: True)
    payload = cf.BibleStudyBody.model_validate(BODY).to_request()
    assert await client.answer("bible_study_ask", payload) == "An answer."
    [req] = captured
    assert req["url"] == PROVIDER.url
    assert set(req["json"]) == {"model", "messages", "temperature", "stream"}
    assert req["json"]["model"] == "some/model:free"
    # The messages are the template applied to the five fields, and nothing more.
    assert req["json"]["messages"] == cf.FEATURES["bible_study_ask"].render(payload)


def test_personal_fields_cannot_get_into_the_payload():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        cf.BibleStudyBody.model_validate({**BODY, **PERSONAL})
    assert set(cf.BibleStudyRequest.__dataclass_fields__) == {
        "reference", "verses", "translation", "question", "reading_level"}
    assert set(cf.PublicVerse.__dataclass_fields__) == {"reference", "text"}


# ------------------------------------------------------------------ the route


@pytest.fixture
def route(monkeypatch):
    from fastapi import FastAPI

    from services.gateway import config as gw_config

    monkeypatch.setattr(gw_config, "INTERNAL_SECRET", SECRET)
    app = FastAPI()
    app.include_router(cf.router)
    return TestClient(app)


def _post(route, body, secret=SECRET):
    return route.post("/internal/cloud-fallback/bible-study", json=body, headers={"X-Internal-Secret": secret})


def test_the_route_answers_with_the_note(route, down, wide_open, sends):
    resp = _post(route, BODY)
    assert resp.status_code == 200
    assert resp.json() == {"answer": "An answer naming Genesis 1:1.", "note": cf.ONLINE_NOTE, "online": True,
                           "feature": "bible_study_ask"}


@pytest.mark.parametrize("field", sorted(PERSONAL))
def test_the_route_refuses_any_personal_field(route, down, wide_open, sends, field):
    resp = _post(route, {**BODY, field: PERSONAL[field]})
    assert resp.status_code == 422 and sends == []


def test_the_route_refuses_extra_fields_inside_a_verse(route, down, wide_open, sends):
    body = {**BODY, "verses": [{**BODY["verses"][0], "note": "NOTE-SECRET"}]}
    assert _post(route, body).status_code == 422 and sends == []


def test_the_route_refuses_another_feature_name(route, down, wide_open, sends):
    assert _post(route, {**BODY, "feature": "chat"}).status_code == 422 and sends == []


def test_the_route_is_internal_only(route, down, wide_open, sends):
    assert _post(route, BODY, secret="wrong").status_code == 403 and sends == []


def test_the_route_does_nothing_while_the_local_ai_is_up(route, wide_open, sends):
    local_ai.LLM.reset()
    resp = _post(route, BODY)
    assert resp.status_code == 409 and sends == []


def test_no_provider_gives_the_caller_its_offline_path(route, down, monkeypatch, sends):
    monkeypatch.setattr(cf, "CLIENT", cf.CloudFallbackClient(frozenset(cf.FEATURES), None, local_ai.LLM.is_down))
    resp = _post(route, BODY)
    assert resp.status_code == 503 and resp.json()["reason"] == "no_provider" and sends == []


def test_a_failing_provider_gives_the_caller_its_offline_path(route, down, wide_open, monkeypatch):
    async def boom(provider, messages):
        raise RuntimeError("openrouter HTTP 429: secret detail")

    monkeypatch.setattr(cf, "_send", boom)
    resp = _post(route, BODY)
    assert resp.status_code == 503 and "secret detail" not in resp.text


# ------------------------------------------------------------------ chat never reaches it


def test_chat_with_the_breaker_open_and_everything_allowlisted_never_goes_online(down, wide_open, sends, monkeypatch):
    from unittest.mock import patch

    from services.gateway import main as gateway_main
    from services.gateway.tests.test_chat_storage_routing import _make_session, client, get_test_settings

    sess = _make_session(get_test_settings(), iter([]))
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    monkeypatch.setattr(gateway_main, "select_system_instruction_for_query", lambda q, m: "# System")
    monkeypatch.setattr(gateway_main.engine, "is_fast_path", lambda *a: False)
    monkeypatch.setattr(gateway_main.engine, "classify", lambda q: ("general", 0.1))
    with patch("aiohttp.ClientSession", return_value=sess), \
            patch("services.gateway.main.shared_http_client", return_value=sess):
        for path, extra in (("/api/chat", {}),
                            ("/v1/chat/completions", {"messages": [{"role": "user", "content": "What does John 3:16 mean?"}]})):
            resp = client.post(path, json={"query": "What does John 3:16 mean?", "stream": False, **extra},
                               headers={"Authorization": "Bearer test-token"})
            assert resp.status_code == 503
    assert sends == []


# ------------------------------------------------------------------ nothing else can reach it

_SERVICES = Path(__file__).resolve().parents[2]


def _importers() -> dict[str, set[str]]:
    """Every non-test module under services/ that imports the fallback, and the names it uses."""
    found: dict[str, set[str]] = {}
    for path in _SERVICES.rglob("*.py"):
        rel = path.relative_to(_SERVICES).as_posix()
        if "/tests/" in f"/{rel}" or path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        imported = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "services.gateway.cloud_fallback" or (
                        node.module in ("services.gateway", ".") and any(a.name == "cloud_fallback" for a in node.names)):
                    imported = True
            elif isinstance(node, ast.Import) and any(a.name == "services.gateway.cloud_fallback" for a in node.names):
                imported = True
        if imported and rel != "gateway/cloud_fallback.py":
            used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Name) and n.value.id == "cloud_fallback"}
            found[rel] = used
    return found


def test_only_the_gateway_startup_wires_it_and_no_feature_calls_it():
    """Guardian (telemetry), app vetting, unlocks, chat and Raven have no way to reach the client:
    the gateway's main module only mounts its route and configures it at startup."""
    assert _importers() == {"gateway/main.py": {"router", "configure"}}
