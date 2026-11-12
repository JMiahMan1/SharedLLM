"""A voice satellite's room reaches Jarvis only from an internal caller (homehub)."""
from types import SimpleNamespace

from services.gateway import main as gateway_main


def _request(secret: str | None):
    headers = {"X-Internal-Secret": secret} if secret is not None else {}
    return SimpleNamespace(headers=headers)


CTX = {"area": "Den", "area_id": "den", "satellite_name": "Den speaker", "asked": "Which light?",
       "extra_system_prompt": "Keep it short.", "unexpected": "dropped"}


def test_voice_context_is_trusted_only_with_the_internal_secret(monkeypatch):
    monkeypatch.setattr(gateway_main, "INTERNAL_SECRET", "s3cret")
    body = {"query": "turn on the light", "voice_context": dict(CTX)}
    assert gateway_main._voice_context_from(_request("wrong"), body) is None
    assert "voice_context" not in body  # never passed on to the model untrusted
    body = {"voice_context": dict(CTX)}
    ctx = gateway_main._voice_context_from(_request("s3cret"), body)
    assert ctx == {"area": "Den", "area_id": "den", "satellite_name": "Den speaker", "asked": "Which light?",
                   "extra_system_prompt": "Keep it short."}


def test_the_note_names_the_room_and_what_was_asked():
    note = gateway_main._voice_context_note({"area": "Den", "area_id": "den", "satellite_name": "Den speaker",
                                             "asked": "Which light?"})
    assert "in the Den (area id den)" in note and '"this room"' in note
    assert "You had just asked them: 'Which light?'" in note


def test_a_satellite_with_no_room_is_told_to_ask():
    note = gateway_main._voice_context_note({"satellite_name": "Spare speaker"})
    assert "no room assigned" in note
    assert gateway_main._voice_context_note({}) == ""
