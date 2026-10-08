"""Workspace chats: a conversation with Jarvis that lives in a workspace tab.

A turn streams as events (``step``, ``thinking``, ``text``, ``tool_call``,
``tool_result``, ``mission``, ``done``, ``error``) and is stored as typed parts
in the shape OpenCode keeps a message in, so the tab can replay a finished turn
exactly as it watched it happen. The conversation so far is sent back to the
model on each turn, which is what lets a person answer Jarvis after a task.
"""
from __future__ import annotations

from typing import Any

import aiohttp

#: How much of the conversation is replayed to the model each turn.
HISTORY_MESSAGES = 12
HISTORY_CHARS = 12_000


def history_from(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The chat so far as model messages: what was asked and what was answered.

    Only the prose goes back -- thinking and tool output stay in the transcript
    for the person, not in the next prompt -- newest kept when it must be cut.
    """
    out: list[dict[str, str]] = []
    for message in messages[-HISTORY_MESSAGES:]:
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        text = "\n".join(
            p.get("text", "") for p in message.get("parts", []) if isinstance(p, dict) and p.get("type") == "text"
        ).strip()
        if text:
            out.append({"role": role, "content": text})
    while out and sum(len(m["content"]) for m in out) > HISTORY_CHARS:
        out.pop(0)
    return out


class TurnTranscript:
    """Folds a turn's streamed events into the parts that are stored for it."""

    def __init__(self) -> None:
        self.parts: list[dict[str, Any]] = []
        self._tools: dict[str, dict[str, Any]] = {}

    def add(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "thinking":
            last = self.parts[-1] if self.parts else None
            if last and last["type"] == "reasoning" and last.get("step") == event.get("step"):
                last["text"] += event.get("text", "")
            else:
                self.parts.append({"type": "reasoning", "text": event.get("text", ""), "step": event.get("step")})
        elif kind == "tool_call":
            if event.get("preamble"):
                self.parts.append({"type": "text", "text": event["preamble"], "step": event.get("step")})
            tool = {"type": "tool", "id": event.get("id"), "name": event.get("name"),
                    "input": event.get("input") or {}, "status": "running"}
            self._tools[str(event.get("id"))] = tool
            self.parts.append(tool)
        elif kind == "tool_result":
            tool = self._tools.get(str(event.get("id")))
            if tool is not None:
                output = event.get("output", "")
                tool["output"] = output
                tool["status"] = "error" if str(output).startswith(("Sorry, I couldn't", "Sorry, I encountered")) else "done"
        elif kind == "mission":
            self.parts.append({"type": "mission", "mission_id": event.get("mission_id"), "status": "queued"})

    def finish(self, answer: str | None, *, status: str = "done", error: str | None = None) -> list[dict[str, Any]]:
        """The stored parts: the final answer is authoritative over streamed text."""
        for tool in self._tools.values():
            if tool["status"] == "running":
                tool["status"] = "aborted" if status == "aborted" else "error"
        parts = [p for p in self.parts]
        if answer:
            parts.append({"type": "text", "text": answer})
        if error:
            parts.append({"type": "error", "text": error})
        return parts


async def identity_chat(
    method: str,
    path: str,
    *,
    identity_url: str,
    auth_header: str | None,
    json: dict | None = None,
    params: dict | None = None,
) -> tuple[int, Any]:
    """Call identity's per-user chat store as the caller (their own chats only)."""
    from services.gateway.main import shared_http_client

    async with shared_http_client() as client:
        resp = await client.request(
            method,
            f"{identity_url}/api/users/me/workspace-chats{path}",
            json=json,
            params=params,
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=aiohttp.ClientTimeout(total=15.0),
        )
        try:
            body = await resp.json(content_type=None)
        except Exception:
            body = {"detail": await resp.text()}
        return resp.status, body
