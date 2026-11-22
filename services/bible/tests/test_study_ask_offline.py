"""A study question while Jarvis's local AI is offline shows the gateway's plain sentence."""
from __future__ import annotations

import pytest

from services.bible import study_ask

OFFLINE = "Jarvis's thinking brain (the local AI) is offline right now."


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self):
        return self._body


async def test_the_offline_sentence_is_passed_on_as_it_is():
    err = await study_ask._refusal(_Resp(503, {"message": OFFLINE, "error": {"message": OFFLINE},
                                                "error_code": "local_ai_offline", "offline": "local_ai"}))
    assert str(err) == OFFLINE


async def test_other_refusals_keep_their_status():
    err = await study_ask._refusal(_Resp(500, {"detail": "boom"}))
    assert "HTTP 500" in str(err)


@pytest.mark.parametrize("body", [{"message": {"role": "assistant", "content": "x"}}, {}])
async def test_an_object_message_is_not_shown_as_text(body):
    err = await study_ask._refusal(_Resp(502, body))
    assert "{" not in str(err)
