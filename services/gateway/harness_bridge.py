"""Safe delegation between Jarvis's four harnesses.

Voice Assistant, Generic Chat, Workspace and Raven Mission each answer in a
different shape (a spoken sentence, a chat reply, a workspace turn, a running
mission), so one harness cannot simply call another's endpoint and hope. This
module is the single, guarded crossing:

* every call carries the caller's ``ResolvedCredentials`` unchanged -- a
  delegation never acquires a privilege its caller did not have;
* ``HARNESS_CALL_DEPTH`` and ``HARNESS_CALL_CHAIN`` (ContextVars) make runaway
  recursion and cycles deterministic refusals rather than stack overflows;
* each operation returns one compact JSON string with the field names the UI
  already renders (``mission_id``, ``workspace_id``, ``summary``, ``status``).

The operations are deliberately thin: they reuse the existing mission enqueue,
workspace-turn, single-turn and notification paths instead of growing a second
implementation of any of them. Imports of those paths are local so this module
stays a leaf (tests import it without constructing the gateway app).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import quote

from services.gateway.schemas import ResolvedCredentials

log = logging.getLogger(__name__)

#: How many harness hops a single request may take. Two is enough for
#: "Raven -> Workspace -> Lookup" and leaves no room for a chain that cannot be
#: explained to the person waiting for the answer.
MAX_HARNESS_DEPTH = 2

HARNESS_CALL_DEPTH: ContextVar[int] = ContextVar("harness_call_depth", default=0)
HARNESS_CALL_CHAIN: ContextVar[tuple[str, ...]] = ContextVar("harness_call_chain", default=())

DEPTH_LIMIT_MESSAGE = "Delegation depth limit (2) reached; answer directly instead."

HARNESSES = ("raven", "workspace", "voice", "chat")

#: The UI's handoff cards are keyed by these five names.
DELEGATION_TOOL_NAMES = (
    "DelegateRavenRequest",
    "DelegateWorkspaceRequest",
    "DelegateLookupRequest",
    "DelegateSubagentRequest",
    "DelegateNotifyRequest",
)

#: Normalized tool name -> bridge operation.
DELEGATION_TOOLS: dict[str, str] = {
    "delegateravenrequest": "delegate_to_raven",
    "delegateworkspacerequest": "delegate_to_workspace",
    "delegatelookuprequest": "delegate_lookup",
    "delegatesubagentrequest": "delegate_explorer_subagent",
    "delegatenotifyrequest": "delegate_notify",
}

# ---------------------------------------------------------------------------
# Read-only firewall (the Explorer subagent)
# ---------------------------------------------------------------------------

#: Tools that only observe. Everything else -- writes, patches, shell, commits,
#: pushes, delegations -- is refused during a read-only turn.
READ_ONLY_ACTIONS: frozenset[str] = frozenset({
    "workspacefilereadrequest",
    "workspacesearchrequest",
    "workspacelistrequest",
    "workspacelintrequest",
    "storagefilereadrequest",
    "storagelistrequest",
    "contextsearchrequest",
    "websearchrequest",
    "webreadrequest",
    "codesearchrequest",
    "entitysearchrequest",
    "haconfigrequest",
    "logbookrequest",
    "mediastatusrequest",
    "llminforequest",
    "capabilityindexrequest",
    # Bare git read verbs are accepted by the dispatcher too.
    "gitstatus",
    "gitdiff",
    "gitlog",
    "gitshow",
    "gitblame",
    "git_status",
    "git_diff",
    "git_log",
    "git_show",
    "git_blame",
})

#: GitOperationRequest is read-only only for these verbs.
READ_ONLY_GIT_ACTIONS: frozenset[str] = frozenset({
    "status", "diff", "log", "show", "blame", "branch", "remote",
})

READ_ONLY_REFUSAL = (
    "That tool is not available in a read-only Explorer turn. Only file reads, "
    "searches, listings, git status/diff/log/show/blame and knowledge lookups "
    "are allowed; nothing was changed."
)


def is_read_only_action(action: str, payload: Any = None) -> bool:
    """Whether ``action`` may run inside the Explorer's read-only firewall."""
    norm = re.sub(r"[\s_]+", "", str(action or "")).lower()
    if norm == "gitoperationrequest":
        body = payload if isinstance(payload, dict) else {}
        verb = str(
            body.get("sub_action")
            or body.get("operation")
            or body.get("git_action")
            or body.get("action")
            or ""
        ).strip().lower()
        verb = re.sub(r"[\s_]+", "", verb)
        if verb in ("gitoperationrequest", "gitoperation"):
            verb = ""
        return verb.replace("-", "_") in READ_ONLY_GIT_ACTIONS
    return norm in READ_ONLY_ACTIONS


# ---------------------------------------------------------------------------
# Identity, chains and refusal
# ---------------------------------------------------------------------------


def normalize_source_harness(raw: str | None, creds: Any = None) -> str:
    """Map the internal turn name (``satellite``) to the harness a person knows."""
    name = str(raw or "").strip().lower()
    if name in ("", "satellite", "single_turn", "single-turn", "assistant"):
        identified = str(_get(creds, "identified_by") or "").strip().lower()
        return "voice" if identified == "voice" else "chat"
    if name in ("mission", "raven"):
        return "raven"
    if name in ("workspace", "voice", "chat"):
        return name
    return name


def _chain_text(chain: tuple[str, ...], *extra: str) -> str:
    return " -> ".join(part for part in (*chain, *extra) if part)


def delegation_refusal(
    source_harness: str,
    target_harness: str,
    *,
    allow_same_harness: bool = False,
) -> str | None:
    """Why this delegation may not run, or None when it may.

    Deterministic on purpose: the same call in the same place always gets the
    same sentence, so a model cannot retry its way past a guardrail.
    """
    depth = HARNESS_CALL_DEPTH.get()
    chain = HARNESS_CALL_CHAIN.get()
    source = normalize_source_harness(source_harness)
    target = normalize_source_harness(target_harness)
    if depth >= MAX_HARNESS_DEPTH:
        return DEPTH_LIMIT_MESSAGE
    if target in chain:
        return (
            "Delegation cycle refused: "
            f"{_chain_text(chain, source, target)} would revisit {target}. "
            "Answer directly instead."
        )
    if target == source and not allow_same_harness:
        return (
            "Delegation cycle refused: "
            f"{_chain_text(chain, source, target)} calls the same harness twice. "
            "Answer directly instead."
        )
    return None


def can_delegate(source_harness: str, target_harness: str) -> bool:
    """Whether the current call stack permits this hop."""
    return delegation_refusal(source_harness, target_harness) is None


@contextmanager
def delegation_context(source_harness: str) -> Iterator[None]:
    """Run one delegation with the depth and chain incremented.

    Both ContextVars are reset in ``finally``: an exception inside a delegated
    turn must not leak a higher depth into the next turn on the same task.
    """
    depth_token = HARNESS_CALL_DEPTH.set(HARNESS_CALL_DEPTH.get() + 1)
    chain_token = HARNESS_CALL_CHAIN.set(
        (*HARNESS_CALL_CHAIN.get(), normalize_source_harness(source_harness))
    )
    try:
        yield
    finally:
        HARNESS_CALL_CHAIN.reset(chain_token)
        HARNESS_CALL_DEPTH.reset(depth_token)


# ---------------------------------------------------------------------------
# Credentials: copied, never escalated
# ---------------------------------------------------------------------------


def _get(creds: Any, name: str, default: Any = None) -> Any:
    if isinstance(creds, dict):
        return creds.get(name, default)
    return getattr(creds, name, default)


def _as_dict(creds: Any) -> dict[str, Any]:
    if creds is None:
        return {}
    if isinstance(creds, dict):
        return dict(creds)
    if hasattr(creds, "model_dump"):
        return creds.model_dump()
    return {k: v for k, v in vars(creds).items() if not k.startswith("_")}


def _as_credentials(creds: Any) -> ResolvedCredentials:
    if isinstance(creds, ResolvedCredentials):
        return creds
    body = _as_dict(creds)
    allowed = set(ResolvedCredentials.model_fields)
    return ResolvedCredentials(**{k: v for k, v in body.items() if k in allowed and k != "user"} | {"user": str(body.get("user") or "")})


def _user(creds: Any) -> str:
    return str(_get(creds, "user") or "").strip()


def _is_admin(creds: Any) -> bool:
    return bool(_get(creds, "is_admin", False))


# ---------------------------------------------------------------------------
# Result packets
# ---------------------------------------------------------------------------


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _ok(**fields: Any) -> str:
    return _json({"status": "SUCCESS", **fields})


def _queued(**fields: Any) -> str:
    return _json({"status": "QUEUED", **fields})


def _refused(reason: str, **fields: Any) -> str:
    return _json({
        "status": "REFUSED",
        "message": reason,
        "summary": reason,
        "error": reason,
        **fields,
    })


def _failed(reason: str, **fields: Any) -> str:
    return _json({
        "status": "ERROR",
        "message": reason,
        "summary": reason,
        "error": reason,
        **fields,
    })


def _cap(text: Any, limit: int) -> str:
    """A compact answer, never a file dump: the caller's context stays clean."""
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _short(text: Any, limit: int = 90) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _handoff_packet(
    *,
    goal: str,
    source_harness: str,
    workspace_id: str | None,
    context_summary: str,
    notify_on_complete: bool,
) -> str:
    """The compact structured brief a receiving harness starts from."""
    chain = (*HARNESS_CALL_CHAIN.get(), normalize_source_harness(source_harness))
    return json.dumps(
        {
            "@type": "HarnessHandoff",
            "goal": _cap(goal, 500),
            "workspace_id": workspace_id,
            "context_summary": _cap(context_summary, 1500),
            "delegated_from": normalize_source_harness(source_harness),
            "harness_call_chain": list(chain),
            "notify_on_complete": bool(notify_on_complete),
        },
        ensure_ascii=False,
    )


def _looks_long(prompt: str) -> bool:
    """Whether a spoken request is really a long-running workspace job."""
    text = str(prompt or "").lower()
    if len(text) > 240:
        return True
    return any(
        signal in text
        for signal in (
            "transcribe", "refactor", "implement", "build", "migrate", "convert",
            "render", "generate", "deploy", "test suite", "run the tests", "audit",
            "review the", "fix the", "install",
        )
    )


# ---------------------------------------------------------------------------
# 1. Raven mission
# ---------------------------------------------------------------------------


async def delegate_to_raven(
    goal: str,
    creds: Any,
    *,
    source_harness: str,
    workspace_id: str | None = None,
    context_summary: str = "",
    notify_on_complete: bool = True,
    model: str | None = None,
) -> str:
    """Queue a Raven mission and return immediately (voice must not wait)."""
    source = normalize_source_harness(source_harness, creds)
    refusal = delegation_refusal(source, "raven")
    if refusal:
        return _refused(refusal, mission_id=None, workspace_id=workspace_id, delegated_from=source)
    text = str(goal or "").strip()
    if not text:
        return _failed("DelegateRavenRequest needs a 'goal'.", delegated_from=source)
    if not _user(creds):
        return _failed("A mission needs a signed-in caller.", delegated_from=source)

    body = _as_dict(creds)
    packet = _handoff_packet(
        goal=text,
        source_harness=source,
        workspace_id=workspace_id,
        context_summary=context_summary,
        notify_on_complete=notify_on_complete,
    )
    try:
        from services.gateway import main as gateway_main

        system = await gateway_main._build_raven_system_prompt(text)
        system = f"{system}\n\n## Inter-harness handoff\n{packet}"
        with delegation_context(source):
            mission = await gateway_main._enqueue_user_mission(
                query=text,
                system=system,
                creds=body,
                coding_model=model,
                workspace_id=workspace_id,
            )
    except Exception as exc:  # a refusal is a sentence, never a stack trace
        log.warning("[harness_bridge] Raven delegation failed: %s", exc)
        return _failed(f"I couldn't start the Raven mission: {exc}", delegated_from=source)

    mission_id = mission.get("id") if isinstance(mission, dict) else None
    if mission_id is None:
        return _failed("Raven did not return a mission id.", delegated_from=source)
    summary = (
        f"Started Raven mission #{mission_id} for {_short(text)}. "
        "I'll notify you when it finishes."
    )
    return _ok(
        mission_id=mission_id,
        workspace_id=workspace_id or (mission.get("workspace_id") if isinstance(mission, dict) else None),
        summary=summary,
        notify_on_complete=bool(notify_on_complete),
        delegated_from=source,
    )


# ---------------------------------------------------------------------------
# 2. Workspace
# ---------------------------------------------------------------------------


async def _workspace_allowed(workspace_id: str, creds: dict[str, Any]) -> tuple[bool, str]:
    """Validate access through workspace_runtime's own list (no new policy).

    The runtime already filters the list to what this caller may open: their
    own, shared default, and (for admins) admin-only workspaces. Reusing it
    means the bridge cannot invent a broader rule than the service enforces.
    """
    user = _user(creds)
    if not user:
        return False, "A workspace delegation needs a signed-in caller."
    try:
        from services.gateway import workspace_api

        status, body = await workspace_api.request(
            f"/workspaces?rag_user={quote(user, safe='')}", {}, method="GET"
        )
    except Exception as exc:
        return False, f"I couldn't check access to that workspace: {exc}"
    if status != 200:
        return False, f"I couldn't check access to that workspace (HTTP {status})."
    items = body.get("workspaces") if isinstance(body, dict) else body
    for item in items or []:
        if isinstance(item, dict) and str(item.get("id")) == str(workspace_id):
            return True, ""
    return False, f"You do not have access to workspace '{workspace_id}'."


async def _workspace_async_turn(
    workspace_id: str,
    prompt: str,
    mode: str,
    creds: dict[str, Any],
    source: str,
    notify_message: str | None = None,
) -> None:
    """Run a long workspace task after the caller already got its acknowledgement."""
    try:
        from services.gateway import main as gateway_main

        result = await gateway_main._run_workspace_turn(workspace_id, creds, prompt, mode)
        answer = _cap(
            (result or {}).get("answer") or (result or {}).get("message") or "The workspace task finished.",
            1500,
        )
        message = notify_message or f"Workspace task finished in '{workspace_id}':\n\n{answer}"
    except Exception as exc:
        message = f"Workspace task failed in '{workspace_id}': {exc}"
    try:
        await delegate_notify(
            message,
            creds,
            source_harness="workspace",
            title="Workspace task finished",
            link=_workspace_link(workspace_id),
        )
    except Exception as exc:  # the task result is already in the mission log
        log.warning("[harness_bridge] workspace completion notice failed: %s", exc)


async def delegate_to_workspace(
    workspace_id: str,
    prompt: str,
    creds: Any,
    *,
    source_harness: str,
    mode: str = "single_task",
    async_mode: bool = False,
) -> str:
    """Answer inside a workspace, bounded and workspace-scoped."""
    source = normalize_source_harness(source_harness, creds)
    # A workspace handing work to a *different* workspace is not a harness cycle;
    # the access check below still decides whether it may touch that workspace.
    refusal = delegation_refusal(source, "workspace", allow_same_harness=(source == "workspace"))
    if refusal:
        return _refused(refusal, workspace_id=workspace_id, delegated_from=source)
    wid = str(workspace_id or "").strip()
    text = str(prompt or "").strip()
    if not wid:
        return _failed("DelegateWorkspaceRequest needs a 'workspace_id'.", delegated_from=source)
    if not text:
        return _failed("DelegateWorkspaceRequest needs a 'prompt'.", workspace_id=wid, delegated_from=source)
    body = _as_dict(creds)
    allowed, reason = await _workspace_allowed(wid, body)
    if not allowed:
        return _refused(reason, workspace_id=wid, delegated_from=source)

    run_async = bool(async_mode) or (source == "voice" and _looks_long(text))
    if run_async:
        with delegation_context(source):
            task = asyncio.create_task(
                _workspace_async_turn(wid, text, mode, body, source)
            )
            _BACKGROUND_TASKS.add(task)
            task.add_done_callback(_BACKGROUND_TASKS.discard)
        return _queued(
            workspace_id=wid,
            summary=(
                f"Started that in workspace '{wid}'. "
                "I'll notify you when it finishes."
            ),
            delegated_from=source,
            async_mode=True,
        )

    try:
        from services.gateway import main as gateway_main

        with delegation_context(source):
            result = await gateway_main._run_workspace_turn(wid, body, text, mode)
    except Exception as exc:
        log.warning("[harness_bridge] Workspace delegation failed: %s", exc)
        return _failed(f"Workspace delegation failed: {exc}", workspace_id=wid, delegated_from=source)

    answer = _cap(
        (result or {}).get("answer") or (result or {}).get("message") or "",
        2000,
    )
    mission_id = (result or {}).get("mission_id")
    if mission_id is not None:
        return _ok(
            mission_id=mission_id,
            workspace_id=wid,
            summary=answer or f"Started Raven mission #{mission_id} from workspace '{wid}'.",
            resolved_mode=(result or {}).get("resolved_mode"),
            delegated_from=source,
        )
    return _ok(
        workspace_id=wid,
        summary=answer or "The workspace turn finished with no answer.",
        resolved_mode=(result or {}).get("resolved_mode"),
        delegated_from=source,
    )


# ---------------------------------------------------------------------------
# 3. Read-only lookup
# ---------------------------------------------------------------------------


async def delegate_lookup(
    query: str,
    creds: Any,
    *,
    source_harness: str,
    domain: str = "",
) -> str:
    """A bounded, read-only household/RAG answer for a coding harness."""
    source = normalize_source_harness(source_harness, creds)
    refusal = delegation_refusal(source, "lookup")
    if refusal:
        return _refused(refusal, delegated_from=source)
    text = str(query or "").strip()
    if not text:
        return _failed("DelegateLookupRequest needs a 'query'.", delegated_from=source)
    domain_text = str(domain or "").strip()
    search = f"{text} ({domain_text})" if domain_text else text
    credentials = _as_credentials(creds)

    try:
        from services.gateway import main as gateway_main
        from services.gateway import orchestrator

        rag_context = await orchestrator._fetch_rag_context(
            search,
            _user(creds) or "default",
            credentials,
            include_curriculum=False,
            include_library=True,
        )
        model = await gateway_main.get_assistant_model()
        with delegation_context(source):
            answer = await orchestrator._single_turn_inference(
                query=search,
                model=model,
                system_prompt=gateway_main.select_system_instruction_for_query(search, model),
                rag_context=rag_context or "",
                history=[],
                creds=credentials,
                harness="satellite",
                budget_path="chat",
                max_turns=1,
                read_only=True,
            )
    except Exception as exc:
        log.warning("[harness_bridge] Lookup delegation failed: %s", exc)
        return _failed(f"Lookup failed: {exc}", delegated_from=source)

    summary = _cap(answer or "No matching household context was found.", 1500)
    return _ok(
        summary=summary,
        domain=domain_text,
        delegated_from=source,
        read_only=True,
    )


# ---------------------------------------------------------------------------
# 4. Explorer / Verifier subagent
# ---------------------------------------------------------------------------

_EXPLORER_PROMPT = """You are the Explorer subagent: a read-only investigator working for another harness.

You may read files, search and grep, list directories, run git status/diff/log/show/blame, and query knowledge. You cannot write or patch files, run a shell command, commit, push, change configuration, or delegate to another harness. If a step would change anything, stop and report what you would need instead.

Return ONE concise findings summary, at most 2,000 characters. Cite exact file paths and line numbers (for example `services/gateway/main.py:14578`) for every claim. Quote only the lines that matter; never dump whole files. State plainly what you could not determine."""


async def delegate_explorer_subagent(
    question: str,
    creds: Any,
    *,
    source_harness: str = "raven",
    workspace_id: str | None = None,
    paths: list[str] | None = None,
    max_turns: int = 6,
    event_callback: Callable[[dict], Any] | None = None,
) -> str:
    """Run an isolated read-only mini-loop and return only its findings.

    Intermediate file reads never reach the caller's context: the subagent's
    own turn is where they live, and the caller receives the condensed answer.
    """
    source = normalize_source_harness(source_harness, creds)
    refusal = delegation_refusal(source, "subagent")
    if refusal:
        return _refused(refusal, workspace_id=workspace_id, delegated_from=source)
    text = str(question or "").strip()
    if not text:
        return _failed("DelegateSubagentRequest needs a 'question'.", delegated_from=source)
    turns = max(1, min(int(max_turns or 6), 6))
    body = _as_dict(creds)
    credentials = _as_credentials(creds)
    path_list = [str(p).strip() for p in (paths or []) if str(p).strip()][:20]

    try:
        from services.gateway import main as gateway_main
        from services.gateway import orchestrator

        workspace_context = ""
        if workspace_id:
            allowed, reason = await _workspace_allowed(str(workspace_id), body)
            if not allowed:
                return _refused(reason, workspace_id=workspace_id, delegated_from=source)
            try:
                workspace_context = await gateway_main._workspace_context(
                    str(workspace_id), body, "single_task"
                )
            except Exception as exc:
                log.debug("[harness_bridge] explorer workspace context unavailable: %s", exc)

        prompt = _EXPLORER_PROMPT
        if path_list:
            prompt += "\n\nStart with these paths:\n" + "\n".join(f"- {p}" for p in path_list)
        if workspace_context:
            prompt += f"\n\nWorkspace context:\n{workspace_context}"

        async def _forward(event: dict) -> None:
            if event_callback is None:
                return
            # A trace listener must never fail the work.
            with contextlib.suppress(Exception):
                await event_callback({
                    "type": "subagent",
                    "phase": event.get("type"),
                    "name": event.get("name"),
                    "question": text,
                    "workspace_id": workspace_id,
                    "detail": event.get("output") or event.get("input") or event.get("text") or "",
                })

        model = await gateway_main.get_assistant_model()
        with delegation_context(source):
            answer = await orchestrator._single_turn_inference(
                query=text,
                model=model,
                system_prompt=prompt,
                rag_context="",
                history=[],
                creds=credentials,
                harness="raven",
                workspace_id=str(workspace_id) if workspace_id else None,
                workspace_context=workspace_context,
                event_callback=_forward if event_callback is not None else None,
                budget_path="chat",
                max_turns=turns,
                read_only=True,
            )
    except Exception as exc:
        log.warning("[harness_bridge] Explorer subagent failed: %s", exc)
        return _failed(f"The Explorer subagent failed: {exc}", delegated_from=source)

    summary = _cap(answer or "The Explorer subagent found nothing to report.", 2000)
    return _ok(
        summary=summary,
        workspace_id=workspace_id,
        max_turns=turns,
        read_only=True,
        delegated_from=source,
    )


# ---------------------------------------------------------------------------
# 5. Notification
# ---------------------------------------------------------------------------


def _workspace_link(workspace_id: str | None) -> dict[str, Any]:
    from services.common import notice_links

    if workspace_id:
        return notice_links.link("workspaces", select=workspace_id)
    return notice_links.link("home")


def _notification_target(
    link: Any,
    *,
    mission_id: int | str | None,
    workspace_id: str | None,
    is_admin: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    """A validated in-app link (or an https page), never a bare string."""
    from services.common import notice_links

    if isinstance(link, dict):
        return notice_links.check(link), None
    if link:
        text = str(link).strip()
        if text.startswith("https://"):
            return None, text
        return notice_links.link_from_href(text), None
    if mission_id is not None:
        if workspace_id:
            return notice_links.link("workspaces", select=workspace_id), None
        if is_admin:
            return notice_links.link("lab", tab="missions"), None
    return notice_links.link("home"), None


async def delegate_notify(
    message: str,
    creds: Any,
    *,
    source_harness: str,
    title: str = "Jarvis",
    target_user: str | None = None,
    link: Any = None,
    mission_id: int | str | None = None,
    workspace_id: str | None = None,
) -> str:
    """Send a completion/update notice to the caller (another user: admin only)."""
    source = normalize_source_harness(source_harness, creds)
    refusal = delegation_refusal(source, "notify")
    if refusal:
        return _refused(refusal, delegated_from=source)
    text = str(message or "").strip()
    if not text:
        return _failed("DelegateNotifyRequest needs a 'message'.", delegated_from=source)
    caller = _user(creds)
    if not caller:
        return _failed("A notification needs a signed-in caller.", delegated_from=source)
    recipient = str(target_user or "").strip() or caller
    admin = _is_admin(creds)
    if recipient != caller and not admin:
        return _refused(
            "Only an admin may notify another user; the notice was not sent.",
            delegated_from=source,
        )

    try:
        from services.common import notify

        target, url = _notification_target(
            link, mission_id=mission_id, workspace_id=workspace_id, is_admin=admin
        )
        payload = notify.notice(
            recipient,
            "raven" if mission_id is not None else "chat",
            str(title or "Jarvis"),
            _cap(text, 1000),
            link=target,
            url=url,
            tag=f"mission:{mission_id}" if mission_id is not None else None,
            data={
                "mission_id": mission_id,
                "workspace_id": workspace_id,
                "delegated_from": source,
            },
            actions=[notify.view_action(target or url, "Open")],
        )
        with delegation_context(source):
            delivered = await notify.send(payload)
    except Exception as exc:
        log.warning("[harness_bridge] Notify delegation failed: %s", exc)
        return _failed(f"The notification could not be sent: {exc}", delegated_from=source)

    href = ""
    if isinstance(payload.get("data"), dict):
        href = str(payload["data"].get("url") or "")
    return _ok(
        summary=f"Notification sent to {recipient}." if delivered else f"Notification for {recipient} was not delivered.",
        user=recipient,
        link=href,
        mission_id=mission_id,
        delivered=bool(delivered),
        delegated_from=source,
    )


#: Keep fire-and-forget workspace turns alive until they finish.
_BACKGROUND_TASKS: set[asyncio.Task] = set()
