"""Home Assistant's sentence triggers, ``conversation.process`` and ``ai_task.generate_data``, on Jarvis.

**Sentence triggers** (``trigger: conversation``). An automation lists sentences in HA's template
syntax: ``[optional words]``, ``(either|or)`` alternatives and ``{slot}`` wildcards that capture
any words. Matching ignores case, punctuation and extra spaces, like HA's default agent.
``<rules>`` (HA's shared expansion rules) are not supported and the automation loads disabled
saying so.

The contract with Jarvis is one call, made before Jarvis answers anything itself:

``POST /hub/conversation/triggers`` (internal secret) ``{"text", "satellite_id"?, "device_id"?,
"conversation_id"?, "language"?}`` -> ``{"matched": false}`` or ``{"matched": true, "response":
"...", "automations": ["automation.x", ...]}``.

Every automation with a matching sentence whose conditions pass runs (HA fires them all). The
reply is the first ``set_conversation_response`` any of them set, in automation order, or "Done"
as in HA. The hub waits up to ``CONVERSATION_WAIT_S`` for the runs; one still going after that
keeps going and the reply is whatever was set by then. A sentence that matches only automations
whose conditions fail counts as not matched, so Jarvis answers it. The gateway asks for every chat
and voice turn (``services/gateway/sentence_triggers.py``); the voice satellites reach Jarvis
through the gateway's ``/api/chat``, so they are covered by the same call.

**conversation.process** sends the text to Jarvis's ``/api/chat`` (``HOMEHUB_VOICE_JARVIS_URL``,
the conversation agent the hub already uses) and answers in HA's response shape.
**ai_task.generate_data** asks Jarvis for the data; with a ``structure`` it asks for one JSON
object with those fields and fails if the answer is not one.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.homehub.auth import bearer
from services.homehub.core import Hub, HubError, ServiceCall

log = logging.getLogger("homehub.conversation")

#: How long a sentence waits for the automations it fired before answering.
CONVERSATION_WAIT_S = 10.0
#: A template that expands to more sentences than this is refused (``[a] [b] [c] ...`` doubles each time).
MAX_EXPANSIONS = 1024
DEFAULT_RESPONSE = "Done"

_PUNCT = re.compile(r"[.,!?;:\"“”¿¡]")
_SPACE = re.compile(r"\s+")


class SentenceError(ValueError):
    """A sentence template HA would accept that the hub cannot match, or one HA would refuse."""


def normalize(text: str) -> str:
    """What a sentence is matched on: no punctuation, single spaces, trimmed (case is ignored when matching)."""
    return _SPACE.sub(" ", _PUNCT.sub(" ", text or "")).strip()


# -- templates -------------------------------------------------------------------------------
# A parsed template is a list of parts; a part is ("text", str), ("slot", name) or ("alt", [[parts], ...]).

def _parse(template: str) -> list[tuple[str, Any]]:
    pos = 0

    def seq(closers: str) -> tuple[list[list[tuple[str, Any]]], str | None]:
        nonlocal pos
        branches: list[list[tuple[str, Any]]] = [[]]
        buf = ""
        while pos < len(template):
            ch = template[pos]
            if ch in closers:
                if buf:
                    branches[-1].append(("text", buf))
                pos += 1
                return branches, ch
            if ch == "|" and closers:
                if buf:
                    branches[-1].append(("text", buf))
                    buf = ""
                branches.append([])
                pos += 1
                continue
            if ch in "([{<":
                if buf:
                    branches[-1].append(("text", buf))
                    buf = ""
                pos += 1
                if ch == "{":
                    end = template.find("}", pos)
                    if end < 0:
                        raise SentenceError(f"{template!r}: '{{' is never closed")
                    name = template[pos:end].strip().split(":", 1)[0].strip()
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                        raise SentenceError(f"{template!r}: {{{name}}} is not a slot name")
                    branches[-1].append(("slot", name))
                    pos = end + 1
                elif ch == "<":
                    end = template.find(">", pos)
                    rule = template[pos:end] if end >= 0 else template[pos:]
                    raise SentenceError(f"{template!r}: the expansion rule <{rule}> is not supported; "
                                        "write its words out, or use a {slot}")
                else:
                    inner, closed = seq(")" if ch == "(" else "]")
                    if closed is None:
                        raise SentenceError(f"{template!r}: '{ch}' is never closed")
                    if ch == "[":
                        inner.append([])  # optional: the empty branch
                    branches[-1].append(("alt", inner))
                continue
            if ch in ")]}>":
                raise SentenceError(f"{template!r}: '{ch}' closes nothing")
            buf += ch
            pos += 1
        if buf:
            branches[-1].append(("text", buf))
        return branches, None

    branches, _ = seq("")
    return branches[0]


def _expand(parts: list[tuple[str, Any]]) -> list[list[tuple[str, Any]]]:
    out: list[list[tuple[str, Any]]] = [[]]
    for kind, value in parts:
        if kind == "alt":
            options = [x for branch in value for x in _expand(branch)]
            out = [a + b for a in out for b in options]
        else:
            out = [[*a, (kind, value)] for a in out]
        if len(out) > MAX_EXPANSIONS:
            raise SentenceError(f"the sentence expands to more than {MAX_EXPANSIONS} variants; split it up")
    return out


@dataclass
class Sentence:
    template: str
    patterns: list[tuple[re.Pattern[str], list[str]]] = field(default_factory=list)

    def match(self, text: str) -> dict[str, str] | None:
        """Slot values (``{}`` without slots) when ``text`` says this sentence, else None."""
        said = normalize(text)
        for pattern, names in self.patterns:
            m = pattern.fullmatch(said)
            if m:
                return {name: m.group(f"s{i}").strip() for i, name in enumerate(names)}
        return None


def compile_sentence(template: str) -> Sentence:
    if not isinstance(template, str) or not normalize(template.replace("{", "").replace("}", "")):
        raise SentenceError(f"{template!r} is not a sentence")
    sentence = Sentence(template)
    seen: set[str] = set()
    for variant in _expand(_parse(template)):
        names = [value for kind, value in variant if kind == "slot"]
        if len(set(names)) != len(names):
            raise SentenceError(f"{template!r}: a slot is named twice in one sentence")
        # The variant as text, a NUL standing for each slot; an optional word left out leaves no gap.
        flat = normalize("".join(value if kind == "text" else "\0" for kind, value in variant))
        if not flat.replace("\0", "").strip() and not names:
            continue
        if flat in seen:
            continue
        seen.add(flat)
        slot = iter(range(len(names)))
        words = []
        for token in flat.split(" "):
            pieces = token.split("\0")
            words.append("".join(re.escape(p) if k == 0 else f"(?P<s{next(slot)}>.+?){re.escape(p)}"
                                 for k, p in enumerate(pieces)))
        regex = r"\s+".join(words)
        sentence.patterns.append((re.compile(regex, re.IGNORECASE), names))
    if not sentence.patterns:
        raise SentenceError(f"{template!r} is not a sentence")
    return sentence


def compile_command(command: Any) -> list[Sentence]:
    """A conversation trigger's ``command`` (one sentence or a list) compiled; SentenceError when one is bad."""
    items = [command] if isinstance(command, str) else list(command or [])
    if not items:
        raise SentenceError("a conversation trigger needs at least one sentence in 'command'")
    return [compile_sentence(str(s)) for s in items]


# -- the hub side of the contract ----------------------------------------------------------

def satellite_device(hub: Hub, satellite_id: str | None) -> str | None:
    ent = hub.entities.get(satellite_id or "")
    return ent.device_id if ent is not None else None


async def run_triggers(runtime: Any, text: str, *, satellite_id: str | None = None, device_id: str | None = None,
                       conversation_id: str | None = None, language: str | None = None) -> dict[str, Any]:
    """The ``/hub/conversation/triggers`` answer for one heard or typed sentence."""
    engine = getattr(runtime, "automations", None)
    if engine is None:
        raise HubError("the automation engine is not running yet")
    device_id = device_id or satellite_device(runtime.hub, satellite_id)
    return await engine.handle_conversation(text, {
        "device_id": device_id, "satellite_id": satellite_id, "conversation_id": conversation_id,
        "language": language or "en"}, wait_s=CONVERSATION_WAIT_S)


def build_router(runtime: Any, auth: Any) -> APIRouter:
    router = APIRouter()

    @router.post("/hub/conversation/triggers")
    async def triggers(request: Request) -> dict[str, Any]:
        tok = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(tok)
        if p is None or not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("text"), str) or not body["text"].strip():
            raise HTTPException(400, detail="The body must be {\"text\": \"what was said\"}")

        def opt(key: str) -> str | None:
            v = body.get(key)
            return str(v)[:200] if isinstance(v, str) and v else None

        try:
            return await run_triggers(runtime, body["text"][:1000], satellite_id=opt("satellite_id"),
                                      device_id=opt("device_id"), conversation_id=opt("conversation_id"),
                                      language=opt("language"))
        except HubError as exc:
            raise HTTPException(503, detail=str(exc)) from exc

    return router


# -- services ------------------------------------------------------------------------------

async def _ask(text: str, context: dict[str, Any]) -> str:
    """One turn with Jarvis, the conversation agent the hub's voice pipeline uses."""
    from services.homehub import voice_pipeline

    settings = voice_pipeline.current_settings()
    if not settings.jarvis_url:
        raise HubError("HOMEHUB_VOICE_JARVIS_URL is not set (the gateway's address, which serves /api/chat)")
    try:
        return await voice_pipeline.BACKENDS.ask(settings, text, context)
    except voice_pipeline.PipelineError as exc:
        raise HubError(str(exc)) from exc


async def conversation_process(hub: Hub, call: ServiceCall) -> dict[str, Any]:
    text = str(call.data.get("text") or "").strip()
    if not text:
        raise HubError("conversation.process needs 'text'")
    agent = call.data.get("agent_id")
    if agent not in (None, "", "conversation.home_assistant", "homeassistant", "jarvis") and \
            not str(agent).startswith("conversation."):
        raise HubError(f"conversation agent {agent!r} does not exist here; conversations go to Jarvis")
    cid = str(call.data.get("conversation_id") or secrets.token_hex(8))
    reply = await _ask(text, {"conversation_id": cid})
    return {"response": {"speech": {"plain": {"speech": reply, "extra_data": None}}, "card": {},
                         "language": call.data.get("language") or "en", "response_type": "action_done",
                         "data": {"targets": [], "success": [], "failed": []}},
            "conversation_id": cid, "continue_conversation": reply.rstrip().endswith("?")}


def _json_object(reply: str) -> dict[str, Any]:
    text = reply.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise HubError(f"ai_task.generate_data: Jarvis did not answer with a JSON object: {reply[:200]!r}")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise HubError(f"ai_task.generate_data: Jarvis's answer is not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise HubError("ai_task.generate_data: Jarvis's answer is not a JSON object")
    return data


async def ai_task_generate_data(hub: Hub, call: ServiceCall) -> dict[str, Any]:
    instructions = str(call.data.get("instructions") or "").strip()
    if not instructions:
        raise HubError("ai_task.generate_data needs 'instructions'")
    if call.data.get("attachments"):
        raise HubError("ai_task.generate_data: attachments are not supported here")
    structure = call.data.get("structure")
    prompt = instructions
    if structure:
        if not isinstance(structure, dict):
            raise HubError("ai_task.generate_data: 'structure' must map field names to their selectors")
        fields = []
        for name, spec in structure.items():
            spec = spec if isinstance(spec, dict) else {}
            desc = spec.get("description") or ""
            kind = next(iter(spec.get("selector") or {"text": None}), "text")
            fields.append(f"- {name} ({kind}{', required' if spec.get('required') else ''}){': ' + desc if desc else ''}")
        prompt = (f"{instructions}\n\nAnswer with only one JSON object with these fields, and nothing else:\n"
                  + "\n".join(fields))
    reply = await _ask(prompt, {"conversation_id": secrets.token_hex(8)})
    if not structure:
        return {"conversation_id": None, "data": reply}
    data = _json_object(reply)
    missing = [k for k, v in structure.items() if isinstance(v, dict) and v.get("required") and k not in data]
    if missing:
        raise HubError(f"ai_task.generate_data: Jarvis's answer lacks the required field(s) {', '.join(missing)}")
    return {"conversation_id": None, "data": data}


def register(hub: Hub) -> None:
    hub.register_service("conversation", "process", conversation_process,
                         {"text": {}, "language": {}, "agent_id": {}, "conversation_id": {}})
    hub.register_service("ai_task", "generate_data", ai_task_generate_data,
                         {"task_name": {}, "instructions": {}, "structure": {}, "entity_id": {}, "attachments": {}})


async def wait_runs(tasks: list[asyncio.Task], timeout: float) -> None:
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)
