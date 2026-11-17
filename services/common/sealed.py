"""Sealed chat messages: the end-to-end encrypted envelope of a private chat.

A private chat between the parents (services/gateway/private_chat.py) keeps
every message as a short human line plus a ``jarvis-envelope`` block of kind
``sealed`` whose payload is AES-GCM ciphertext made on the sender's device
(services/ui/src/lib/privateChatCrypto.ts). No server holds the key. The
gateway stores these itself (services/gateway/private_chat_messages.py); the
first chats posted them to a Talk room, which is kept after their history moved.

Anything on a server that reads Talk messages (notifications, the @Jarvis
poller, room context for the assistant, family games) uses ``is_sealed`` to
skip such a message, and the store uses it (with a stricter shape check) to
refuse anything else: there is nothing in it a server can read, and treating
ciphertext as text would only put noise in a notification or a prompt.
"""
from __future__ import annotations

import re

SEALED_KIND = "sealed"

#: What other Talk clients (the Nextcloud app) show in place of the ciphertext.
SEALED_LINE = "Encrypted message"

_SEALED = re.compile(r"```jarvis-envelope\s*\{[^`]*\"kind\"\s*:\s*\"sealed\"", re.S)


def is_sealed(message: str | None) -> bool:
    """Whether a Talk message is a private chat's encrypted envelope."""
    return bool(message) and bool(_SEALED.search(str(message)))
