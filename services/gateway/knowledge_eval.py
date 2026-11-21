"""Does the model fetch what it needs? An eval of the on-demand context design.

With information behind tools instead of in the prompt, the risk moves: a model
that does not call the tool answers from nothing. ``data/knowledge_eval.json``
holds generic household questions, the tool each should call first (or none),
any argument that call must carry, and what the pre-fetch may read.

Two halves:

* ``check_routing`` is deterministic: each question's pre-fetch reads only the
  collections the case allows, and every expected tool is in the knowledge map
  for that surface. Run in CI (test_knowledge_eval.py).
* ``run`` asks a real model, through the real single-turn path (persona, tool
  guide, knowledge map, routed pre-fetch), and records the first tool call. The
  call is not executed. Needs the live stack:
  ``python scripts/eval_knowledge_tools.py --model <name>`` inside the gateway.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

CASES_PATH = os.path.join(os.path.dirname(__file__), "data", "knowledge_eval.json")


def load_cases(*, hub: bool) -> list[dict[str, Any]]:
    """The cases that apply with (or without) a home hub."""
    with open(CASES_PATH, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    return [c for c in cases if hub or not c.get("needs_hub")]


def _norm(name: str | None) -> str:
    return re.sub(r"[\s_]+", "", str(name or "")).lower()


def score(case: dict[str, Any], call: dict[str, Any] | None) -> dict[str, Any]:
    """Whether the first tool call (``None``: the model answered directly) is the right one."""
    want = case.get("tool")
    got = None
    payload: dict[str, Any] = {}
    if call:
        got = call.get("action") or call.get("tool") or call.get("name")
        raw_payload = call.get("payload", call)
        payload = raw_payload if isinstance(raw_payload, dict) else {}
    if want is None:
        ok = got is None
        return {"id": case["id"], "ok": ok, "want": None, "got": got,
                "why": "" if ok else f"called {got} for a question that needs no tool"}
    if _norm(got) != _norm(want):
        return {"id": case["id"], "ok": False, "want": want, "got": got,
                "why": "answered without a tool" if got is None else f"called {got}"}
    for key, value in (case.get("args") or {}).items():
        have = payload.get(key)
        if key == "action":
            have = have or payload.get("sub_action") or payload.get("device_action")
        if _norm(have) != _norm(value):
            return {"id": case["id"], "ok": False, "want": want, "got": got,
                    "why": f"{key} was {have!r}, wanted {value!r}"}
    return {"id": case["id"], "ok": True, "want": want, "got": got, "why": ""}


def check_routing(*, hub: bool) -> list[str]:
    """Problems with the deterministic half (empty means every case holds)."""
    from services.gateway import knowledge_map, orchestrator

    problems: list[str] = []
    shown = knowledge_map.knowledge_map_for("single_turn", is_admin=False, guide="")
    for case in load_cases(hub=hub):
        got = orchestrator._collections_for_query(case["question"])
        allowed = set(case.get("prefetch") or [])
        extra = [c for c in got if c not in allowed]
        if extra:
            problems.append(f"{case['id']}: pre-fetches {extra}, allowed {sorted(allowed)}")
        if case.get("tool") and case["tool"] not in shown:
            problems.append(f"{case['id']}: {case['tool']} is not in the knowledge map")
    return problems


class _Stop(Exception):
    pass


async def run(model: str, *, system_prompt: str, user: str = "eval", hub: bool = True,
              limit: int | None = None) -> dict[str, Any]:
    """Ask ``model`` every case through the real single-turn path; score the first call."""
    from services.gateway import orchestrator
    from services.gateway.schemas import ResolvedCredentials

    creds = ResolvedCredentials(user=user)
    results = []
    real_execute = orchestrator._execute_single_tool
    for case in load_cases(hub=hub)[:limit]:
        seen: list[dict] = []

        async def capture(action, tool_data, query, creds, workspace_id=None, _seen=seen):
            _seen.append({"action": action, "payload": tool_data.get("payload", tool_data)})
            raise _Stop

        orchestrator._execute_single_tool = capture
        try:
            context = await orchestrator._fetch_rag_context(case["question"], user, creds, include_curriculum=False)
            await orchestrator._single_turn_inference(case["question"], model, system_prompt, context, [], creds)
        except _Stop:
            pass
        finally:
            orchestrator._execute_single_tool = real_execute
        results.append(score(case, seen[0] if seen else None))
    passed = sum(r["ok"] for r in results)
    return {"model": model, "cases": len(results), "passed": passed,
            "pass_rate": round(passed / len(results), 3) if results else None, "results": results}
