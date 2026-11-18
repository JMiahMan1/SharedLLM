# Private chat reply reminders

When one parent writes in the private chat, the other reads it and does not
answer, Jarvis sends the reader one gentle reminder a while later:
"Sam is waiting to hear back from you", or "Sam asked you something and is
waiting to hear back" when the reader's own phone saw a question in it.

The Nudge stays as it was: the sender's button, only while their newest message
is still unread. A reminder is for the other case, a message that was read and
left.

Code: `services/gateway/private_chat_reminders.py` (rules, routes, the pass),
`services/gateway/background_worker.py` (`_private_chat_reminders_loop`),
`services/ui/src/lib/privateChatReminders.ts` (the question check on the
device), the switch in `PrivateChatSecurity.tsx`.

## The rules

A reminder goes to B about A's message when all of these hold:

| Rule | Detail |
|---|---|
| Read and not answered | The newest live message is A's, and B's read marker is at or past it. B sending anything ends it; A sending something newer starts a new clock. |
| The delay | 6 hours (`PRIVATE_CHAT_REMINDER_DELAY_S`), or 2 hours (`PRIVATE_CHAT_REMINDER_QUESTION_DELAY_S`) when B's device flagged a question. Counted from the later of when A sent it and when the server first saw B had read it, so reading late never sets one off at once. |
| Once per message | Each message gets at most one reminder. |
| At most 2 a day | Per person, per household day (`PRIVATE_CHAT_REMINDER_DAILY_MAX`). |
| Quiet hours | None between 21:30 and 07:30 in the household time zone (`PRIVATE_CHAT_REMINDER_QUIET`, `off` for none; the zone is the household setting, docs/CONFIG.md "Time zone"). One that falls due inside them goes after they end, if still due. |
| Not old news | None for a message more than 3 days old (`PRIVATE_CHAT_REMINDER_MAX_AGE_S`). |
| Your choice | Each parent can turn their own reminders off in the chat's settings (Lock and password, "Reply reminders"). On by default, kept on the server, so it holds on every device. |
| Only a live chat | Nothing for a deleted chat, one still on Talk, or one whose members are not both parents any more. |

The gateway's background worker checks every 5 minutes
(`PRIVATE_CHAT_REMINDER_CHECK_S`). A reminder is recorded before it is sent and
released if telemetry does not take it, so a restart or a second gateway never
sends it twice and a failed one is tried again on the next pass.

## The notice

Category and kind `nudge` (the Nudges channel and its sound on the phone, and
the full-screen card when Jarvis is open), priority 3 (one lower than a nudge),
tag `private-reminder:<token>` with `replace`, so a newer reminder replaces an
older one. It opens the chat. It never contains a word of the message.

## What leaves the device, and what the server keeps

The chat is end to end encrypted: the server never sees the text, so it cannot
tell whether a message asks something. B's app can. When B opens the unlocked
chat, it looks at A's last three messages since B last wrote (text, picture
captions, photo comments, edits) for a question mark or a question-shaped
sentence ("are you home", "what time is dinner", "let me know"). If one asks
something, it sends:

```
POST /api/private-chat/chats/<token>/expects-reply
{"message_id": <the newest message's id>, "expects_reply": true}
```

That is all: one bit for one message id, only for the partner's newest message
(anything else is refused). No text, nothing about which message asked or how.
Nothing is sent when no question was found.

The server keeps, per chat, in `PRIVATE_CHAT_DIR/reminders/<token>.json`:

- `enabled`: each member's on/off choice;
- `expects`: per member, the id of the newest message their device flagged;
- `read_seen`: per member, the id and time the server first saw they had read the newest message;
- `reminded`: per member, the ids they were reminded about (the last 20);
- `sent`: per member, the times of reminders in the last two days.

It already knew who sent each message and when, and each member's read marker;
the new metadata is the flag bit and when a message was first seen read. The
file goes with the chat when the chat is deleted. It is not part of the
encrypted backup (it holds nothing worth restoring).

## Routes

- `GET /api/private-chat/chats/<token>/reminders`: `{enabled, available, delay_s, question_delay_s, quiet, daily_max}` for the caller.
- `PUT /api/private-chat/chats/<token>/reminders` `{"enabled": bool}`: the caller's own switch.
- `POST /api/private-chat/chats/<token>/expects-reply`: the flag above (`false` clears it).

Members only (anyone else gets 404, as for every private chat route).

## Settings

In `.env`, read once when the gateway starts. A malformed value stops the
gateway with a message naming the variable.

| Variable | Default | |
|---|---|---|
| `PRIVATE_CHAT_REMINDERS` | `1` | `0` turns the feature off (the switch is then hidden). |
| `PRIVATE_CHAT_REMINDER_DELAY_S` | `21600` | 6 hours. |
| `PRIVATE_CHAT_REMINDER_QUESTION_DELAY_S` | `7200` | 2 hours. |
| `PRIVATE_CHAT_REMINDER_QUIET` | `21:30-07:30` | Household local time; `off` for none. |
| `PRIVATE_CHAT_REMINDER_DAILY_MAX` | `2` | Per person per household day. |
| `PRIVATE_CHAT_REMINDER_MAX_AGE_S` | `259200` | 3 days. |
| `PRIVATE_CHAT_REMINDER_CHECK_S` | `300` | How often the worker looks. |

No household time zone configured means no reminders, and the gateway log says
how to set one.
