"""Family chat -> phone notifications.

Every few seconds each family member's own Talk conversation list is read
with their own Nextcloud login (so it covers every conversation *they* are
in, not only the system account's), and a conversation whose last message is
new, unread, and from someone else becomes one notice in that person's
telemetry outbox. The phone picks outbox notices up natively (a long poll to
``/api/telemetry/notifications/wait``) and shows them as system notifications;
nothing goes through an outside push service.

A person's first pass only records where every conversation stands, so a
new family member does not get their whole history as a burst of
notifications; after that, a conversation never seen before is a new one and
its first message is announced.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp

log = logging.getLogger("gateway.chat_notify")

#: Redis hash per user: conversation token -> last message id already handled.
SEEN_KEY = "chat:notify:seen:{user}"
#: Set once a user's first pass is recorded; after that, a conversation never
#: seen before is a *new* one (someone just messaged them) and is announced.
PRIMED_KEY = "chat:notify:primed:{user}"
POLL_SECONDS = 5
_FENCE = re.compile(r"```.*?(```|$)", re.S)
_PLACEHOLDER = re.compile(r"\{(file|mention-[a-z]+\d*|[a-z-]+\d*)\}")
#: Jarvis's answers are posted with the asker's login and marked by an
#: ``assistant`` envelope (execution ``_assistant_post``).
_ASSISTANT = re.compile(r"```jarvis-envelope\s*\{[^`]*\"kind\"\s*:\s*\"assistant\"", re.S)


def preview(message: str | None) -> str:
    """The message as a notification line: no envelope blocks or placeholders.

    Talk writes rich objects as ``{file}`` / ``{mention-user1}`` with the
    details in separate parameters, which a notification cannot show.
    """
    text = _FENCE.sub("", message or "")
    attachment = "{file}" in text
    text = _PLACEHOLDER.sub(lambda m: "@" + m.group(1).split("-")[-1] if m.group(1).startswith("mention") else "", text)
    text = re.sub(r"\s+", " ", text).strip()[:180]
    if attachment:
        return f"📎 {text}" if text else "Sent an attachment"
    return text or "New message"


def notice_for(conv: dict[str, Any], me: str) -> dict[str, Any] | None:
    """The notice a conversation summary deserves for ``me``, or None."""
    actor = (conv.get("last_message_actor_id") or "").lower()
    if not conv.get("last_message_id") or conv.get("last_message_system"):
        return None
    # Jarvis answering is news for everyone -- including the person who asked,
    # whose login it is posted under (so Talk counts it as already read).
    jarvis = bool(_ASSISTANT.search(conv.get("last_message") or ""))
    if not jarvis and (actor == me.lower() or not int(conv.get("unread_messages") or 0)):
        return None
    sender = "Jarvis" if jarvis else conv.get("last_message_actor") or actor or "Someone"
    room = conv.get("display_name") or sender
    one_to_one = conv.get("type") == 1
    body = preview(conv.get("last_message"))
    unread = int(conv.get("unread_messages") or 0)
    return {
        "kind": "chat",
        "title": sender if one_to_one else room,
        "body": body if one_to_one else f"{sender}: {body}",
        "data": {
            "token": conv.get("token"),
            "message_id": conv.get("last_message_id"),
            "conversation": room,
            "sender": sender,
            "unread": unread,
            "mention": bool(conv.get("unread_mention")),
            "url": f"/family?chat={conv.get('token')}",
        },
    }


async def _json(resp: aiohttp.ClientResponse) -> Any:
    try:
        return await resp.json(content_type=None)
    except Exception:
        return None


async def check_once(r, client: aiohttp.ClientSession, *, identity: str, execution: str,
                     telemetry: str, secret: str) -> int:
    """One pass over every family member; returns how many notices were sent."""
    headers = {"X-Internal-Secret": secret}
    timeout = aiohttp.ClientTimeout(total=20)
    async with client.get(f"{identity}/api/users", headers=headers, timeout=timeout) as resp:
        users = await _json(resp) if resp.status == 200 else None
    if not isinstance(users, list):
        return 0
    sent = 0
    for u in users:
        username = (u or {}).get("username")
        if not username or not u.get("nextcloud_user") or u.get("is_system_default"):
            continue
        try:
            sent += await _check_user(r, client, username, identity=identity, execution=execution,
                                      telemetry=telemetry, headers=headers, timeout=timeout)
        except Exception as e:  # one person's failure must not silence the rest
            log.warning(f"[chat-notify] {username}: {type(e).__name__}: {e}")
    return sent


async def _check_user(r, client, username: str, *, identity, execution, telemetry, headers, timeout) -> int:
    async with client.post(f"{identity}/api/resolve", json={"rag_user": username},
                           headers=headers, timeout=timeout) as resp:
        creds = await _json(resp) if resp.status == 200 else None
    if not isinstance(creds, dict) or not creds.get("nextcloud_user"):
        return 0
    async with client.post(f"{execution}/execute/talk", json={"user_context": creds, "action": "list"},
                           headers=headers, timeout=timeout) as resp:
        body = await _json(resp) if resp.status == 200 else None
    if not isinstance(body, dict) or body.get("status") != "SUCCESS":
        return 0
    conversations = (body.get("detail") or {}).get("conversations") or []
    key = SEEN_KEY.format(user=username)
    seen = await r.hgetall(key) or {}
    primed = bool(await r.get(PRIMED_KEY.format(user=username)))
    me = creds["nextcloud_user"]
    sent = 0
    for conv in conversations:
        token, msg_id = conv.get("token"), conv.get("last_message_id")
        if not token or not msg_id:
            continue
        before = seen.get(token)
        if before is not None and int(before) >= int(msg_id):
            continue
        notice = None if (before is None and not primed) else notice_for(conv, me)
        if notice is not None:
            payload = {"user": username, **notice, "once": f"chat:{token}:{msg_id}"}
            async with client.post(f"{telemetry}/api/telemetry/notify", json=payload,
                                   headers=headers, timeout=timeout) as resp:
                if resp.status != 200:
                    # Not marked handled: the next pass retries, and the
                    # "once" key keeps a retry from showing it twice. Marking
                    # it first lost every message that arrived while
                    # telemetry was restarting.
                    log.warning(f"[chat-notify] notify {username} failed: HTTP {resp.status}")
                    continue
                sent += 1
        # A user's first pass only records where each conversation stands.
        await r.hset(key, token, str(msg_id))
    if not primed:
        await r.set(PRIMED_KEY.format(user=username), "1")
    return sent
