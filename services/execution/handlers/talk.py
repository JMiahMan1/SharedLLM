import base64
import json
import logging
import urllib.parse
from typing import Any, Optional
from uuid import uuid4

try:
    from ..schemas import ExecutionResult, TalkRequest
    from ..personal_data import resolve_personal_data_provider
except ImportError:
    from schemas import ExecutionResult, TalkRequest
    from personal_data import resolve_personal_data_provider

log = logging.getLogger("execution.talk")

TALK_UPLOAD_DIR = "Talk Uploads"


def _decode_audio(audio_base64: str) -> bytes:
    """Decodes base64 audio, handling potential data URL prefixes and padding."""
    try:
        b64_data = audio_base64
        if "," in b64_data:
            b64_data = b64_data.split(",")[1]
        
        # Add padding if necessary
        missing_padding = len(b64_data) % 4
        if missing_padding:
            b64_data += '=' * (4 - missing_padding)
            
        return base64.b64decode(b64_data)
    except Exception as e:
        log.error(f"Audio decoding failed: {e}")
        return b""


def _conversation_summary(conversation: dict[str, Any]) -> dict[str, Any]:
    last_message = conversation.get("lastMessage") or {}
    return {
        "id": conversation.get("id"),
        "token": conversation.get("token"),
        "display_name": conversation.get("displayName") or conversation.get("name") or conversation.get("token"),
        "name": conversation.get("name"),
        "description": conversation.get("description"),
        "unread_messages": conversation.get("unreadMessages", 0),
        "last_activity": conversation.get("lastActivity"),
        "last_message": last_message.get("message"),
    }


def _message_summary(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": message.get("id"),
        "token": message.get("token"),
        "actor_type": message.get("actorType"),
        "actor_id": message.get("actorId"),
        "actor_display_name": message.get("actorDisplayName") or message.get("actorId") or "Unknown",
        "timestamp": message.get("timestamp"),
        "message_type": message.get("messageType"),
        "system_message": message.get("systemMessage"),
        "message": message.get("message"),
        "is_replyable": message.get("isReplyable", False),
    }


async def handle_talk(req: TalkRequest) -> ExecutionResult:
    provider = resolve_personal_data_provider(req.user_context)
    if not provider:
        return ExecutionResult(status="FAILURE", message="Nextcloud Talk credentials missing.", service="talk")

    action = req.action
    log.info("[talk] action=%s user=%s token=%s target=%s", action, req.user_context.user, req.token, req.target_user)

    try:
        if action == "list":
            ok, data, message = provider.request(
                "GET",
                "/ocs/v2.php/apps/spreed/api/v4/room",
                params={"includeStatus": "true"},
            )
            if not ok:
                return ExecutionResult(status="FAILURE", message=message or "Failed to load conversations.", service="talk_list")
            conversations = [_conversation_summary(item) for item in (data or [])]
            return ExecutionResult(
                status="SUCCESS",
                message=f"Loaded {len(conversations)} conversation(s).",
                service="talk_list",
                detail={"conversations": conversations},
            )

        if action == "open":
            if req.token:
                ok, data, message = provider.request(
                    "GET",
                    f"/ocs/v2.php/apps/spreed/api/v4/room/{urllib.parse.quote(req.token)}",
                )
            elif req.target_user:
                ok, data, message = provider.request(
                    "POST",
                    "/ocs/v2.php/apps/spreed/api/v4/room",
                    data={"roomType": "1", "invite": req.target_user},
                )
            else:
                return ExecutionResult(status="FAILURE", message="A conversation token or target user is required.", service="talk_open")
            if not ok:
                return ExecutionResult(status="FAILURE", message=message or "Failed to open conversation.", service="talk_open")
            return ExecutionResult(
                status="SUCCESS",
                message=f"Opened conversation {_conversation_summary(data).get('display_name')}.",
                service="talk_open",
                detail={"conversation": _conversation_summary(data)},
            )

        if action == "messages":
            if not req.token:
                return ExecutionResult(status="FAILURE", message="Conversation token is required.", service="talk_messages")
            ok, data, message = provider.request(
                "GET",
                f"/ocs/v2.php/apps/spreed/api/v1/chat/{urllib.parse.quote(req.token)}",
                params={"lookIntoFuture": "0", "limit": str(req.limit)},
            )
            if not ok:
                return ExecutionResult(status="FAILURE", message=message or "Failed to load messages.", service="talk_messages")
            messages = [_message_summary(item) for item in (data or [])]
            return ExecutionResult(
                status="SUCCESS",
                message=f"Loaded {len(messages)} message(s).",
                service="talk_messages",
                detail={"messages": messages},
            )

        if action == "send":
            if not req.token or not req.message:
                return ExecutionResult(status="FAILURE", message="Conversation token and message are required.", service="talk_send")
            ok, data, message = provider.request(
                "POST",
                f"/ocs/v2.php/apps/spreed/api/v1/chat/{urllib.parse.quote(req.token)}",
                data={"message": req.message},
            )
            if not ok:
                return ExecutionResult(status="FAILURE", message=message or "Failed to send message.", service="talk_send")
            return ExecutionResult(
                status="SUCCESS",
                message="Chat message sent.",
                service="talk_send",
                detail={"message_record": _message_summary(data)},
            )

        if action == "send_voice":
            if not req.token:
                return ExecutionResult(status="FAILURE", message="Conversation token is required.", service="talk_send_voice")

            audio_bytes = b""
            if req.text_to_voice:
                log.info(f"[talk] Generating TTS for: {req.text_to_voice}")
                import edge_tts
                import asyncio
                communicate = edge_tts.Communicate(req.text_to_voice, "en-US-GuyNeural")
                # We need to run this in a temporary file or buffer
                audio_bytes = b""
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_bytes += chunk["data"]
                req.mime_type = "audio/mpeg"
                if not req.file_name:
                    req.file_name = f"tts-{uuid4().hex[:8]}.mp3"
            elif req.audio_base64:
                audio_bytes = _decode_audio(req.audio_base64)
            else:
                return ExecutionResult(status="FAILURE", message="Either text_to_voice or audio_base64 is required.", service="talk_send_voice")

            if not audio_bytes:
                return ExecutionResult(status="FAILURE", message="Failed to generate or decode audio.", service="talk_send_voice")

            extension = ".mp3" if (req.mime_type or "").endswith("mpeg") else (".m4a" if (req.mime_type or "").endswith("mp4") else ".webm")
            file_name = provider.sanitize_filename(req.file_name or f"voice-{uuid4().hex}{extension}", f"voice-{uuid4().hex}{extension}")
            remote_path = f"{TALK_UPLOAD_DIR}/{file_name}"
            provider.ensure_directory(TALK_UPLOAD_DIR)

            upload_resp = provider.upload_file(remote_path, audio_bytes, req.mime_type or "audio/webm")
            if upload_resp.status_code not in {200, 201, 204}:
                return ExecutionResult(
                    status="FAILURE",
                    message=f"Failed to upload audio ({upload_resp.status_code}).",
                    service="talk_send_voice",
                )

            metadata = {"messageType": "voice-message"}
            if req.caption:
                metadata["caption"] = req.caption

            ok, data, message = provider.request(
                "POST",
                "/ocs/v2.php/apps/files_sharing/api/v1/shares",
                data={
                    "shareType": "10",
                    "shareWith": req.token,
                    "path": f"/{remote_path}",
                    "referenceId": uuid4().hex,
                    "talkMetaData": json.dumps(metadata),
                },
            )
            if not ok:
                return ExecutionResult(status="FAILURE", message=message or "Failed to send voice message.", service="talk_send_voice")

            return ExecutionResult(
                status="SUCCESS",
                message="Voice message sent to Nextcloud Talk.",
                service="talk_send_voice",
                detail={"share": data, "path": f"/{remote_path}"},
            )

        return ExecutionResult(status="FAILURE", message=f"Action {action} not implemented.", service="talk")

    except Exception as exc:
        log.error("Talk error: %s", exc)
        return ExecutionResult(status="FAILURE", message=f"Talk error: {exc}", service="talk")
