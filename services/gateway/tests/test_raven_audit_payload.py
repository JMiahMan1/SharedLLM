"""Regression tests for Raven audit-log payload round-tripping.

The "Tweak or Fix Results" (refine) flow re-ingests a mission's ``output_log``
to reconstruct prior conversation turns. If the payload is persisted as a
Python ``repr`` (single-quoted, invalid JSON) or dropped, the reconstruction
emits ``{"action": ..., "payload": null}`` assistant turns that teach the
model to mirror the malformed ``null`` payload — the exact failure behind
``'NoneType' object does not support item assignment``.
"""

import json

from services.gateway.agent_loop import normalize_audit_log


def _raw_audit_log() -> list[dict]:
    return [
        {"type": "action", "data": "Executing Tool: workspacefilereadrequest", "timestamp": 1.0},
        {"type": "action_payload", "data": json.dumps({"path": "main.py"}), "timestamp": 1.1},
        {"type": "result_success", "data": "Read 10 lines", "timestamp": 1.2},
        {"type": "action", "data": "Executing Tool: workspaceshellrequest", "timestamp": 2.0},
        {"type": "action_payload", "data": json.dumps({"command": "ruff check ."}), "timestamp": 2.1},
        {"type": "result_error", "data": "F821 undefined name", "timestamp": 2.2},
    ]


def test_normalize_audit_log_stores_valid_json_payload():
    out = normalize_audit_log(_raw_audit_log())
    # Every result event must carry the tool payload as parseable JSON.
    for ev in out:
        assert ev["type"] in ("result_success", "result_error")
        payload = ev["payload"]
        # Must be valid JSON (NOT a Python repr like "{'path': 'main.py'}").
        assert isinstance(payload, str)
        parsed = json.loads(payload)
        assert isinstance(parsed, dict)


def test_normalize_audit_log_payload_round_trips_arguments():
    out = normalize_audit_log(_raw_audit_log())
    by_tool = {ev["tool"]: json.loads(ev["payload"]) for ev in out}
    assert by_tool["workspacefilereadrequest"] == {"path": "main.py"}
    assert by_tool["workspaceshellrequest"] == {"command": "ruff check ."}


def test_normalize_audit_log_handles_missing_payload():
    log = [
        {"type": "action", "data": "Executing Tool: workspaceshellrequest", "timestamp": 1.0},
        {"type": "result_error", "data": "boom", "timestamp": 1.1},
    ]
    out = normalize_audit_log(log)
    assert out[0]["payload"] is None
