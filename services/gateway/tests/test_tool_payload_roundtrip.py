"""Every field a tool's request schema declares survives the single-turn normalizer.

On 2026-11-24 "Set volume to 100%" reached the model, which called
``{"tool": "MediaTransportRequest", "command": "volume_up", ...}``; the normalizer
treated ``command`` as a routing key and dropped it, so execution answered 422
(command missing) three times and Jarvis said it could not change the volume.
"""

import json
import typing

import pytest
from pydantic import BaseModel

from services.execution import schemas as exec_schemas
from services.gateway.agent_loop import extract_action_json
from services.gateway.orchestrator import SINGLE_TURN_TOOL_ENDPOINTS, normalize_single_turn_call
from services.gateway.tool_registry import _RAVEN_TOOL_TABLE

# Fields the gateway fills in itself, never from the model.
_GATEWAY_OWNED = {"user_context", "workspace_id"}


def _schema(name: str) -> type[BaseModel] | None:
    obj = getattr(exec_schemas, name, None)
    return obj if isinstance(obj, type) and issubclass(obj, BaseModel) else None


# The structural validator (agent_loop._valid_structured_tool) refuses a patch
# without "content", which this schema does not have: a separate, older gap.
_NOT_ROUND_TRIPPED = {"WorkspaceFilePatchRequest"}


def _routed_tools() -> list[str]:
    return [
        row[0] for row in _RAVEN_TOOL_TABLE
        if row[0].lower() in SINGLE_TURN_TOOL_ENDPOINTS and _schema(row[0]) is not None
        and row[0] not in _NOT_ROUND_TRIPPED
    ]


def _sample(name: str, field: str, annotation) -> object:
    """A value the normalizer must pass through untouched."""
    literals = [a for a in typing.get_args(annotation) if typing.get_origin(a) is typing.Literal]
    if typing.get_origin(annotation) is typing.Literal:
        literals = [annotation]
    if literals:
        values = typing.get_args(literals[0])
        # A tool's own verb (not one the normalizer also reads as an HA action).
        return next((v for v in values if v not in ("play", "pause", "stop", "list", "send")), values[0])
    if field in ("path", "file_path", "relative_path"):
        return f"notes/{name.lower()}.txt"
    return f"{name.lower()}-{field}-value"


def _fields(name: str) -> dict[str, object]:
    model = _schema(name)
    assert model is not None
    return {
        field: _sample(name, field, info.annotation)
        for field, info in model.model_fields.items()
        if field not in _GATEWAY_OWNED
    }


def _normalize(raw: dict) -> dict:
    data = extract_action_json(json.dumps(raw))
    assert data is not None, raw
    return normalize_single_turn_call(data)


def _received(name: str, payload: dict) -> dict:
    """The payload as execution's request model reads it (after its alias mapping)."""
    model = _schema(name)
    aliases = getattr(model, "handle_common_aliases", None)
    return aliases(dict(payload)) if aliases else dict(payload)


@pytest.mark.parametrize("shape", ["flat", "payload"])
@pytest.mark.parametrize("name", _routed_tools())
def test_every_schema_field_reaches_execution(name, shape):
    fields = _fields(name)
    raw = {"tool": name, **fields} if shape == "flat" else {"tool": name, "payload": fields}
    out = _normalize(raw)
    assert out["action"].lower() == name.lower()
    received = _received(name, out["payload"])
    lost = {k: v for k, v in fields.items() if received.get(k) != v}
    assert not lost, f"{name} lost fields on the way to execution: {lost}"


def test_the_logged_volume_call_reaches_execution_with_its_command():
    out = _normalize({"tool": "MediaTransportRequest", "command": "volume_set", "volume_level": 1.0,
                      "entity_id": "media_player.office_echo"})
    assert out["action"].lower() == "mediatransportrequest"
    assert out["payload"]["command"] == "volume_set"
    assert out["payload"]["volume_level"] == 1.0
    assert out["payload"]["entity_id"] == "media_player.office_echo"
    exec_schemas.MediaTransportRequest(user_context={"user": "u"}, **out["payload"])


def test_routing_keys_stay_out_and_same_named_fields_stay_in():
    out = _normalize({"tool": "TimerRequest", "action": "cancel", "id": "t1", "type": "timer"})
    assert out["action"].lower() == "timerrequest"
    assert out["payload"]["action"] == "cancel"
    assert out["payload"]["id"] == "t1"
    assert out["payload"]["type"] == "timer"
    assert "tool" not in out["payload"]
