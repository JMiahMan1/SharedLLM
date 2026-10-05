"""Every Raven payload hint must describe fields the target model accepts.

The hint text is what an LLM sees before composing a tool payload. When it
names a field the Pydantic model does not have, the model silently drops the
unknown key (``extra: ignore``) and the call fails later with a 422 for the
missing required field. This test keeps the hints and the schemas in lockstep:

* every hinted key exists on the model (or is one of its aliases),
* every required model field is hinted (directly or through its alias),
* execution tools either have a model or sit in the documented no-model list,
* no table entry quietly targets a service this test does not know about.
"""

from __future__ import annotations

import re

import pytest

from services.execution import schemas as execution_schemas
from services.gateway.tool_registry import SVC_EXECUTION, _RAVEN_TOOL_TABLE

NO_MODEL_TOOLS = {
    "StorageListRequest": "no execution route; storage listing is handled by the gateway",
    "STTRequest": "raw JSON handler reads file_path/path/filename/audio_file",
    "AiCapabilitiesRequest": "GET/POST route without a body model",
}

NON_EXECUTION_TOOLS = {
    "WorkspaceCreateRequest",
    "WorkspaceSettingsUpdateRequest",
    "WorkspaceBootstrapRequest",
    "ContextSearchRequest",
    "StorageIndexRequest",
    "StorageTextToAudioRequest",
    "ControlPlaneRequest",
}

INJECTED_FIELDS = {"user_context", "workspace_id"}

_HINT_RE = re.compile(r"\s*payload fields:\s*(.*?)(?:\.\s|\.$)", re.DOTALL)


def _hinted_fields(hint: str) -> set[str]:
    match = _HINT_RE.match(hint)
    assert match, f"hint lacks a 'payload fields:' sentence: {hint!r}"
    body = match.group(1).strip()
    if body.startswith("(none required"):
        return set()
    return {part.strip() for part in body.split(",") if part.strip()}


def _model_for(name: str):
    return getattr(execution_schemas, name, None)


EXECUTION_ENTRIES = [row for row in _RAVEN_TOOL_TABLE if row[1] == SVC_EXECUTION]


def test_every_table_entry_is_accounted_for():
    for name, service, *_rest in _RAVEN_TOOL_TABLE:
        if service == SVC_EXECUTION:
            assert name not in NON_EXECUTION_TOOLS, (
                f"{name} moved to the execution service but is still listed as non-execution"
            )
        else:
            assert name in NON_EXECUTION_TOOLS, (
                f"{name} targets {service!r}; add it to NON_EXECUTION_TOOLS (with a "
                "reason) or validate it here"
            )


@pytest.mark.parametrize("entry", EXECUTION_ENTRIES, ids=lambda row: row[0])
def test_execution_payload_hint_matches_schema(entry):
    name, _service, _method, _path, _requires_ws, _desc, hint = entry
    model = _model_for(name)

    if name in NO_MODEL_TOOLS:
        assert model is None, (
            f"{name} now has a model; remove it from NO_MODEL_TOOLS so the hint is validated"
        )
        return

    assert model is not None, (
        f"{name} has no model in services/execution/schemas.py; add it to NO_MODEL_TOOLS "
        "with a reason if the handler really takes raw JSON"
    )

    hinted = _hinted_fields(hint)
    valid = set(model.model_fields)
    for field in model.model_fields.values():
        if field.alias:
            valid.add(field.alias)

    unknown = hinted - valid
    assert not unknown, f"{name}: hint names nonexistent fields {sorted(unknown)}"

    missing = []
    for field_name, field in model.model_fields.items():
        if not field.is_required() or field_name in INJECTED_FIELDS:
            continue
        accepted = {field_name}
        if field.alias:
            accepted.add(field.alias)
        if not accepted & hinted:
            missing.append(field_name)
    assert not missing, f"{name}: hint omits required fields {sorted(missing)}"
