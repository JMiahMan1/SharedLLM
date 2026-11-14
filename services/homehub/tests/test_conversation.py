"""Tag and sentence (conversation) triggers, set_conversation_response, conversation.process and ai_task."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.homehub import conversation, voice_pipeline
from services.homehub.automation import AutomationEngine, FakeClock
from services.homehub.core import EntityEntry, Hub, HubError
from services.homehub.tests.test_automation import START, FakeStore, auto, record, run, settle


@pytest.fixture(name="hub")
def _hub() -> Hub:
    return Hub(time_zone="Etc/GMT+7")


@pytest.fixture(name="engine")
async def _engine(hub: Hub):
    eng = AutomationEngine(hub, FakeStore(), clock=FakeClock(START))
    yield eng
    await eng.stop()


# -- sentence templates ----------------------------------------------------------------------


@pytest.mark.parametrize(("template", "said", "slots"), [
    ("movie time", "Movie time!", {}),
    ("movie time", "  movie   TIME. ", {}),
    ("turn [on] the porch light[s]", "turn the porch lights", {}),
    ("turn [on] the porch light[s]", "turn on the porch light", {}),
    ("(start|begin) the [evening] routine", "begin the routine", {}),
    ("I'm leaving for {place}", "I'm leaving for the school run", {"place": "the school run"}),
    ("set {room} to {level} percent", "Set the den to 40 percent?", {"room": "the den", "level": "40"}),
    ("[please] {thing}", "please water the plants", {"thing": "water the plants"}),
])
def test_sentences_match_like_ha(template, said, slots):
    assert conversation.compile_sentence(template).match(said) == slots


@pytest.mark.parametrize(("template", "said"), [
    ("movie time", "movie time now"),
    ("turn [on] the porch light[s]", "turn on porch light"),
    ("(start|begin) the routine", "stop the routine"),
    ("I'm leaving for {place}", "I'm leaving for"),
    ("movie time", "moviet ime"),
])
def test_sentences_do_not_match_other_words(template, said):
    assert conversation.compile_sentence(template).match(said) is None


@pytest.mark.parametrize("bad", ["", "   ", "turn on <name>", "(open", "close]", "{}", "{a} and {a}", None])
def test_bad_templates_are_refused(bad):
    with pytest.raises(conversation.SentenceError):
        conversation.compile_sentence(bad)


def test_a_template_that_expands_too_far_is_refused():
    with pytest.raises(conversation.SentenceError, match="variants"):
        conversation.compile_sentence(" ".join(f"[w{i}]" for i in range(12)) + " go")


def test_a_command_needs_a_sentence():
    with pytest.raises(conversation.SentenceError):
        conversation.compile_command([])
    assert len(conversation.compile_command(["a", "b"])) == 2
    assert len(conversation.compile_command("a")) == 1


# -- tag trigger ----------------------------------------------------------------------------

async def test_tag_trigger_fires_for_its_tag_and_device(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    await run(engine, [auto([{"trigger": "tag", "tag_id": "tag-front-door", "device_id": ["phone-a"]}],
                            [{"action": "test.note", "data": {"t": "{{ trigger.tag_id }}",
                                                              "d": "{{ trigger.device_id }}",
                                                              "p": "{{ trigger.platform }}"}}])])
    hub.bus.fire("tag_scanned", {"tag_id": "tag-other", "device_id": "phone-a"})
    hub.bus.fire("tag_scanned", {"tag_id": "tag-front-door", "device_id": "phone-b"})
    hub.bus.fire("tag_scanned", {"tag_id": "tag-front-door", "device_id": "phone-a"})
    await settle()
    assert calls == [("test.note", {"t": "tag-front-door", "d": "phone-a", "p": "tag"})]


async def test_tag_trigger_without_a_device_takes_any(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    await run(engine, [auto([{"platform": "tag", "tag_id": ["t1", "t2"]}], [{"action": "test.note"}],
                            mode="parallel")])
    hub.bus.fire("tag_scanned", {"tag_id": "t2", "device_id": None})
    hub.bus.fire("tag_scanned", {"tag_id": "t1", "device_id": "phone-z"})
    await settle()
    assert len(calls) == 2


async def test_tag_trigger_without_tag_id_loads_disabled(hub: Hub, engine: AutomationEngine) -> None:
    await run(engine, [auto([{"trigger": "tag"}], [{"action": "test.note"}])])
    assert "tag trigger without tag_id" in (engine.automations["a1"].unsupported_reason or "")


# -- conversation trigger -----------------------------------------------------------------

SAY = [{"action": "test.note", "data": {"room": "{{ trigger.slots.room }}", "s": "{{ trigger.sentence }}",
                                        "dev": "{{ trigger.device_id }}"}},
       {"set_conversation_response": "Done with the {{ trigger.slots.room }}"}]


async def test_a_matching_sentence_runs_the_automation_and_answers(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    await run(engine, [auto([{"trigger": "conversation", "command": ["tidy [up] the {room}", "clean {room}"]}], SAY)])
    out = await engine.handle_conversation("Tidy up the den.", {"device_id": "dev-sat"})
    assert out == {"matched": True, "response": "Done with the den", "automations": ["automation.test"]}
    assert calls == [("test.note", {"room": "den", "s": "Tidy up the den.", "dev": "dev-sat"})]


async def test_no_match_and_failed_conditions_leave_it_to_jarvis(hub: Hub, engine: AutomationEngine) -> None:
    record(hub, "test.note")
    await run(engine, [auto([{"trigger": "conversation", "command": "movie time"}], [{"action": "test.note"}],
                            conditions=[{"condition": "template", "value_template": "{{ false }}"}])])
    assert await engine.handle_conversation("what is the weather", {}) == {"matched": False}
    assert await engine.handle_conversation("movie time", {}) == {"matched": False}


async def test_without_a_response_the_reply_is_done(hub: Hub, engine: AutomationEngine) -> None:
    record(hub, "test.note")
    await run(engine, [auto([{"trigger": "conversation", "command": "movie time"}], [{"action": "test.note"}])])
    assert (await engine.handle_conversation("movie time", {}))["response"] == "Done"


async def test_every_matching_automation_runs_and_the_first_reply_wins(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    await run(engine, [
        auto([{"trigger": "conversation", "command": "good night"}], [{"action": "test.note", "data": {"n": 1}}],
             id="a1", alias="First"),
        auto([{"trigger": "conversation", "command": "good night"}],
             [{"action": "test.note", "data": {"n": 2}}, {"set_conversation_response": "Sleep well"}],
             id="a2", alias="Second"),
    ])
    out = await engine.handle_conversation("good night", {})
    assert out["response"] == "Sleep well" and len(out["automations"]) == 2
    assert sorted(c[1]["n"] for c in calls) == [1, 2]


async def test_a_slow_run_answers_with_what_it_has_said_so_far(hub: Hub, engine: AutomationEngine) -> None:
    record(hub, "test.note")
    gate = asyncio.Event()

    async def slow(_hub: Hub, _call: Any) -> None:
        await gate.wait()

    hub.register_service("test", "slow", slow)
    await run(engine, [auto([{"trigger": "conversation", "command": "start the film"}],
                            [{"set_conversation_response": "Starting"}, {"action": "test.slow"}])])
    out = await engine.handle_conversation("start the film", {}, wait_s=0.05)
    assert out["response"] == "Starting"
    gate.set()


async def test_set_conversation_response_outside_a_sentence_does_nothing(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    await run(engine, [auto([{"trigger": "event", "event_type": "go"}],
                            [{"set_conversation_response": "x"}, {"action": "test.note"}])])
    hub.bus.fire("go", {})
    await settle()
    assert len(calls) == 1 and engine.automations["a1"].last_error is None


async def test_a_bad_sentence_loads_the_automation_disabled(hub: Hub, engine: AutomationEngine) -> None:
    await run(engine, [auto([{"trigger": "conversation", "command": "turn on <name>"}], [{"action": "test.note"}])])
    assert "expansion rule" in (engine.automations["a1"].unsupported_reason or "")


async def test_a_stopped_engine_matches_nothing(hub: Hub, engine: AutomationEngine) -> None:
    await engine.load([auto([{"trigger": "conversation", "command": "movie time"}], [{"action": "test.note"}])],
                      {}, [])
    assert await engine.handle_conversation("movie time", {}) == {"matched": False}


# -- the API Jarvis calls ---------------------------------------------------------------------

class _Auth:
    def authenticate(self, tok: str | None) -> Any:
        return SimpleNamespace(is_admin=True) if tok == "internal" else None


async def test_triggers_route_names_the_satellite_device(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    hub.entities["assist_satellite.den"] = EntityEntry(entity_id="assist_satellite.den", platform="esphome",
                                                       device_id="dev-den")
    await run(engine, [auto([{"trigger": "conversation", "command": "movie time"}],
                            [{"action": "test.note", "data": {"d": "{{ trigger.device_id }}"}}])])
    rt = SimpleNamespace(hub=hub, automations=engine)
    out = await conversation.run_triggers(rt, "movie time", satellite_id="assist_satellite.den")
    assert out["matched"] and calls == [("test.note", {"d": "dev-den"})]
    with pytest.raises(HubError, match="not running"):
        await conversation.run_triggers(SimpleNamespace(hub=hub, automations=None), "movie time")


def test_triggers_route_checks_auth_and_body() -> None:
    h = Hub()

    class Engine:
        async def handle_conversation(self, text, ctx, wait_s):
            return {"matched": text == "hi", "ctx": ctx}

    app = FastAPI()
    app.include_router(conversation.build_router(SimpleNamespace(hub=h, automations=Engine()), _Auth()))
    c = TestClient(app)
    assert c.post("/hub/conversation/triggers", json={"text": "hi"}).status_code == 401
    h = {"Authorization": "Bearer internal"}
    assert c.post("/hub/conversation/triggers", json={"text": ""}, headers=h).status_code == 400
    assert c.post("/hub/conversation/triggers", json=["hi"], headers=h).status_code == 400
    r = c.post("/hub/conversation/triggers", json={"text": "hi", "conversation_id": "c9"}, headers=h).json()
    assert r["matched"] is True and r["ctx"]["conversation_id"] == "c9"
    app2 = FastAPI()
    app2.include_router(conversation.build_router(SimpleNamespace(hub=h, automations=None), _Auth()))
    assert TestClient(app2).post("/hub/conversation/triggers", json={"text": "hi"}, headers=h).status_code == 503


# -- conversation.process and ai_task.generate_data ------------------------------------------

@pytest.fixture
def jarvis(monkeypatch):
    asked: list[tuple[str, dict]] = []
    replies: list[str] = []
    settings = voice_pipeline.VoiceSettings(jarvis_url="http://jarvis.invalid", internal_secret="x")
    monkeypatch.setattr(voice_pipeline, "current_settings", lambda: settings)

    async def ask(_s, text, ctx):
        asked.append((text, ctx))
        return replies.pop(0)

    monkeypatch.setattr(voice_pipeline.BACKENDS, "ask", ask)
    return SimpleNamespace(asked=asked, replies=replies, settings=settings)


async def test_conversation_process_answers_in_ha_shape(jarvis) -> None:
    h = Hub()
    conversation.register(h)
    jarvis.replies.append("The den light is on. Anything else?")
    out = await h.call_service("conversation", "process", {"text": "turn on the den light"}, return_response=True)
    r = out["response"]
    assert r["response"]["speech"]["plain"]["speech"] == "The den light is on. Anything else?"
    assert r["continue_conversation"] is True and r["conversation_id"]
    assert jarvis.asked[0][0] == "turn on the den light"


async def test_conversation_process_fails_fast(jarvis) -> None:
    h = Hub()
    conversation.register(h)
    with pytest.raises(HubError, match="needs 'text'"):
        await h.call_service("conversation", "process", {"text": " "})
    with pytest.raises(HubError, match="does not exist"):
        await h.call_service("conversation", "process", {"text": "hi", "agent_id": "some_other_agent"})
    jarvis.settings.jarvis_url = ""
    with pytest.raises(HubError, match="HOMEHUB_VOICE_JARVIS_URL"):
        await h.call_service("conversation", "process", {"text": "hi"})


async def test_conversation_process_reports_jarvis_errors(jarvis, monkeypatch) -> None:
    h = Hub()
    conversation.register(h)

    async def down(*_a):
        raise voice_pipeline.PipelineError("intent-failed", "Jarvis is unreachable")

    monkeypatch.setattr(voice_pipeline.BACKENDS, "ask", down)
    with pytest.raises(HubError, match="unreachable"):
        await h.call_service("conversation", "process", {"text": "hi"})


async def test_ai_task_text_and_structured(jarvis) -> None:
    h = Hub()
    conversation.register(h)
    jarvis.replies.append("A calm, cloudy morning.")
    out = await h.call_service("ai_task", "generate_data", {"task_name": "t", "instructions": "Describe the day"},
                                 return_response=True)
    assert out["response"]["data"] == "A calm, cloudy morning."
    jarvis.replies.append('Here you go:\n```json\n{"count": 2, "summary": "two parcels"}\n```')
    structure = {"count": {"selector": {"number": {}}, "required": True}, "summary": {"selector": {"text": None}}}
    out = await h.call_service("ai_task", "generate_data", {"task_name": "t", "instructions": "Count the parcels",
                                                              "structure": structure}, return_response=True)
    assert out["response"]["data"] == {"count": 2, "summary": "two parcels"}
    assert "count (number, required)" in jarvis.asked[-1][0]


@pytest.mark.parametrize(("reply", "match"), [
    ("no json here", "JSON object"),
    ("{not json}", "not valid JSON"),
    ('{"summary": "x"}', "required field"),
])
async def test_ai_task_refuses_answers_that_do_not_fit(jarvis, reply, match) -> None:
    h = Hub()
    conversation.register(h)
    jarvis.replies.append(reply)
    structure = {"count": {"selector": {"number": {}}, "required": True}}
    with pytest.raises(HubError, match=match):
        await h.call_service("ai_task", "generate_data", {"instructions": "Count", "structure": structure})


async def test_ai_task_fails_fast_on_what_it_cannot_do(jarvis) -> None:
    h = Hub()
    conversation.register(h)
    with pytest.raises(HubError, match="instructions"):
        await h.call_service("ai_task", "generate_data", {"task_name": "t"})
    with pytest.raises(HubError, match="attachments"):
        await h.call_service("ai_task", "generate_data", {"instructions": "x", "attachments": [{"media": "a"}]})
    with pytest.raises(HubError, match="structure"):
        await h.call_service("ai_task", "generate_data", {"instructions": "x", "structure": ["count"]})


def test_the_editor_offers_the_tag_trigger():
    from services.homehub.automation import UNSUPPORTED_TRIGGERS
    from services.homehub.automation_editor import TRIGGER_SPECS

    tag = next(s for s in TRIGGER_SPECS if s["type"] == "tag")
    assert [f["name"] for f in tag["fields"]] == ["tag_id", "device_id"]
    assert not {"tag", "conversation"} & UNSUPPORTED_TRIGGERS
