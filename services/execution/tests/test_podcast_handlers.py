"""Tests for the podcast / speaker execution handlers.

These three handlers are thin by design: they resolve a workspace, hand the work
to alpaca, and translate whatever comes back into an ``ExecutionResult``. So the
tests are about the *translation* - which failure maps to which status, what is
sent upstream, and what the mission is told - rather than about the audio itself
(alpaca's own suite covers that).

aiohttp is faked with a recorder rather than mocked module-wide, so a handler
that forgets to pass the internal secret, drops a field, or calls the wrong port
shows up as a diff on the recorded requests.
"""

from __future__ import annotations

import base64
import sys
import types
from pathlib import Path

import pytest

from services.execution.handlers import podcast as H
from services.execution.schemas import (
    ListVoicesRequest,
    PodcastRenderRequest,
    SpeakerIdentifyRequest,
    UserContext,
)

UC = UserContext(user="u1", is_admin=False)


# --------------------------------------------------------------------- aiohttp


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status = status
        self._payload = payload
        self._text = text

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


class _Client:
    """Records (verb, url, kwargs) and replays a queued list of responses."""

    def __init__(self, responses):
        self._queue = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _next(self):
        if not self._queue:
            raise AssertionError(f"unexpected extra HTTP call; recorded {self.calls}")
        return self._queue.pop(0)

    def request(self, verb):
        async def call(url, **kwargs):
            self.calls.append((verb, url, kwargs))
            return self._next()

        return call

    def get(self, url, **kwargs):
        return self.request("GET")(url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST")(url, **kwargs)


def _install_aiohttp(monkeypatch, responses):
    client = _Client(responses)
    module = types.ModuleType("aiohttp")
    module.ClientSession = lambda *a, **k: client
    module.ClientTimeout = lambda **k: ("timeout", k)
    monkeypatch.setitem(sys.modules, "aiohttp", module)
    return client


@pytest.fixture
def ws(monkeypatch, tmp_path):
    """A resolved workspace whose root exists, so only real errors surface."""
    root = tmp_path / "ws"
    root.mkdir()

    async def resolve(workspace_id, uc=None, *a, **k):
        if workspace_id != "w1":
            raise ValueError(f"unknown workspace {workspace_id}")
        return str(root), "w1"

    monkeypatch.setattr(H, "_resolve_workspace_info", resolve)
    return root


def _render_req(**kw):
    kw.setdefault("script", "HOST A: hi")
    return PodcastRenderRequest(workspace_id="w1", user_context=UC, **kw)


# ------------------------------------------------------------- podcast_render


@pytest.mark.asyncio
async def test_render_happy_path_saves_the_episode(ws, monkeypatch):
    data_uri = "data:audio/wav;base64," + base64.b64encode(b"RIFFfake").decode()
    http = _install_aiohttp(
        monkeypatch, [_Resp(200, {"data_uri": data_uri, "duration_s": 61.0, "turn_count": 2}), _Resp(200, {})]
    )

    res = await H.handle_podcast_render(_render_req())

    assert res.status == "SUCCESS"
    assert res.detail["duration_s"] == 61.0
    assert res.detail["output_path"] == "podcast.wav"
    render, save = http.calls
    assert render[1].endswith("/api/podcast/render")
    assert save[1].endswith("/files/write")
    assert save[2]["json"]["content_base64"] == base64.b64encode(b"RIFFfake").decode()


@pytest.mark.asyncio
async def test_render_forwards_the_optional_fields_only_when_set(ws, monkeypatch):
    data_uri = "data:audio/wav;base64,QUJD"
    http = _install_aiohttp(monkeypatch, [_Resp(200, {"data_uri": data_uri})] * 2)

    await H.handle_podcast_render(
        _render_req(pair_id="duo_deep", bed_preset="lofi_calm", voice_profiles={"host_clone_duo_deep_a": "p1"})
    )
    body = http.calls[0][2]["json"]
    assert body["pair_id"] == "duo_deep"
    assert body["bed_preset"] == "lofi_calm"
    assert body["voice_profiles"] == {"host_clone_duo_deep_a": "p1"}
    # Always asks for the data URI: the handler has no way to stream a file
    # back into a workspace any other way.
    assert body["return_data_uri"] is True

    http2 = _install_aiohttp(monkeypatch, [_Resp(200, {"data_uri": data_uri})] * 2)
    await H.handle_podcast_render(_render_req())
    bare = http2.calls[0][2]["json"]
    assert "pair_id" not in bare and "bed_preset" not in bare and "voice_profiles" not in bare


@pytest.mark.asyncio
async def test_render_honours_an_explicit_output_path(ws, monkeypatch):
    http = _install_aiohttp(monkeypatch, [_Resp(200, {"data_uri": "data:audio/wav;base64,QUJD"})] * 2)
    res = await H.handle_podcast_render(_render_req(output_path="episodes/s01.wav"))
    assert res.detail["output_path"] == "episodes/s01.wav"
    assert http.calls[1][2]["json"]["relative_path"] == "episodes/s01.wav"


@pytest.mark.asyncio
async def test_render_surfaces_the_mix_warnings(ws, monkeypatch):
    """A cross-gender clone or a truncated music sting arrives as a warning. The
    mission must see it - otherwise Raven confidently reports a broken episode
    as clean."""
    payload = {"data_uri": "data:audio/wav;base64,QUJD", "warnings": ["clone is cross-gender"]}
    _install_aiohttp(monkeypatch, [_Resp(200, payload)] * 2)
    res = await H.handle_podcast_render(_render_req())
    assert res.detail["warnings"] == ["clone is cross-gender"]
    assert "1 warning" in res.message


@pytest.mark.asyncio
async def test_an_empty_script_never_reaches_alpaca(ws, monkeypatch):
    http = _install_aiohttp(monkeypatch, [])
    res = await H.handle_podcast_render(_render_req(script="   "))
    assert res.status == "FAILURE"
    assert "empty" in res.message
    assert http.calls == [], "a blank script must not cost a 30-minute render"


@pytest.mark.asyncio
async def test_an_unresolvable_workspace_fails_before_rendering(ws, monkeypatch):
    http = _install_aiohttp(monkeypatch, [])
    req = PodcastRenderRequest(workspace_id="nope", user_context=UC, script="HOST A: hi")
    res = await H.handle_podcast_render(req)
    assert res.status == "FAILURE"
    assert "nope" in res.message
    assert http.calls == []


@pytest.mark.asyncio
async def test_a_dashboard_error_is_reported_with_its_status(ws, monkeypatch):
    _install_aiohttp(monkeypatch, [_Resp(502, None, text="bed synthesis failed")])
    res = await H.handle_podcast_render(_render_req())
    assert res.status == "FAILURE"
    assert "502" in res.message and "bed synthesis failed" in res.message


@pytest.mark.asyncio
async def test_a_response_without_audio_fails_rather_than_saving_nothing(ws, monkeypatch):
    _install_aiohttp(monkeypatch, [_Resp(200, {"duration_s": 0.0})])
    res = await H.handle_podcast_render(_render_req())
    assert res.status == "FAILURE"
    assert "no audio" in res.message


@pytest.mark.asyncio
async def test_a_malformed_data_uri_is_rejected_before_the_save(ws, monkeypatch):
    http = _install_aiohttp(monkeypatch, [_Resp(200, {"data_uri": "data:audio/wav;base64,"})])
    res = await H.handle_podcast_render(_render_req())
    assert res.status == "FAILURE"
    assert "malformed" in res.message
    assert len(http.calls) == 1, "must not POST an empty body to the workspace"


@pytest.mark.asyncio
async def test_a_failed_workspace_save_is_distinct_from_a_failed_render(ws, monkeypatch):
    """The episode exists but could not be stored. Saying 'render failed' would
    send the agent back to rewrite a script that was fine."""
    _install_aiohttp(monkeypatch, [_Resp(200, {"data_uri": "data:audio/wav;base64,QUJD"}), _Resp(403, None, text="quarantined")])
    res = await H.handle_podcast_render(_render_req())
    assert res.status == "FAILURE"
    assert "rendered but" in res.message and "quarantined" in res.message


@pytest.mark.asyncio
async def test_the_save_carries_the_internal_secret_and_the_user_context(ws, monkeypatch):
    http = _install_aiohttp(monkeypatch, [_Resp(200, {"data_uri": "data:audio/wav;base64,QUJD"})] * 2)
    await H.handle_podcast_render(_render_req())
    save = http.calls[1][2]
    assert save["headers"]["X-Internal-Secret"] == H.INTERNAL_SECRET
    assert save["json"]["user_context"]["user"] == "u1"


# ----------------------------------------------------------- speaker_identify


def _write(root, name, data=b"RIFFfake"):
    (root / name).write_bytes(data)
    return name


@pytest.mark.asyncio
async def test_identify_happy_path_reports_the_match(ws, monkeypatch):
    _write(ws, "doorbell.wav")
    _install_aiohttp(monkeypatch, [_Resp(200, {"matched": "p1", "matched_name": "Ada", "score": 0.82, "runner_up": "Bob"})])
    res = await H.handle_speaker_identify(
        SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="doorbell.wav")
    )
    assert res.status == "SUCCESS"
    assert "Ada" in res.message and "Bob" in res.message
    assert res.detail["score"] == 0.82


@pytest.mark.asyncio
async def test_identify_goes_to_the_audio_server_not_the_dashboard(ws, monkeypatch):
    """Speaker identification needs OpenVoice's reference encoder, which only
    exists in the audio-server container. Routing it through the dashboard
    would 404."""
    _write(ws, "clip.wav")
    http = _install_aiohttp(monkeypatch, [_Resp(200, {"matched_name": "Ada", "score": 0.9})])
    await H.handle_speaker_identify(SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="clip.wav"))
    assert http.calls[0][1].endswith("/api/voices/identify")
    assert str(H.ALPACA_AUDIO_URL) in http.calls[0][1]


@pytest.mark.asyncio
async def test_identify_sends_the_derived_threshold_by_default(ws, monkeypatch):
    """Omitting it is the point: the audio server derives one from how much its
    own speakers vary. Sending 0.0 would silently mean 'match anyone'."""
    _write(ws, "clip.wav")
    http = _install_aiohttp(monkeypatch, [_Resp(200, {"matched_name": "Ada", "score": 0.9})])
    await H.handle_speaker_identify(SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="clip.wav"))
    assert "threshold" not in http.calls[0][2]["json"]


@pytest.mark.asyncio
async def test_identify_passes_an_explicit_threshold_through(ws, monkeypatch):
    _write(ws, "clip.wav")
    http = _install_aiohttp(monkeypatch, [_Resp(200, {"matched_name": "Ada", "score": 0.9})])
    await H.handle_speaker_identify(
        SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="clip.wav", threshold=0.62)
    )
    assert http.calls[0][2]["json"]["threshold"] == 0.62


@pytest.mark.asyncio
async def test_a_non_match_still_succeeds_and_returns_the_ranking(ws, monkeypatch):
    """An agent asking "who is this?" needs "nobody you know, but the closest is
    X" - a FAILURE would throw that away and invite a pointless re-record."""
    _write(ws, "clip.wav")
    payload = {"matched": None, "score": 0.21, "candidates": [{"name": "Ada", "score": 0.21}, {"name": "Bob", "score": 0.18}]}
    _install_aiohttp(monkeypatch, [_Resp(200, payload)])
    res = await H.handle_speaker_identify(
        SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="clip.wav")
    )
    assert res.status == "SUCCESS"
    assert "does not match" in res.message and "Ada" in res.message
    assert len(res.detail["candidates"]) == 2


@pytest.mark.asyncio
async def test_no_enrolled_voices_tells_the_agent_to_enrol_one(ws, monkeypatch):
    _write(ws, "clip.wav")
    _install_aiohttp(monkeypatch, [_Resp(404, None, text="no enrolled voices")])
    res = await H.handle_speaker_identify(
        SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="clip.wav")
    )
    assert res.status == "FAILURE"
    assert "enrolled" in res.message


@pytest.mark.asyncio
async def test_too_little_speech_is_distinct_from_nobody_enrolled(ws, monkeypatch):
    _write(ws, "clip.wav")
    _install_aiohttp(monkeypatch, [_Resp(422, None, text="not enough speech")])
    res = await H.handle_speaker_identify(
        SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="clip.wav")
    )
    assert "not enough speech" in res.message
    assert "enrolled" not in res.message


@pytest.mark.asyncio
async def test_a_missing_clip_fails_before_any_upstream_call(ws, monkeypatch):
    http = _install_aiohttp(monkeypatch, [])
    res = await H.handle_speaker_identify(
        SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="ghost.wav")
    )
    assert res.status == "FAILURE"
    assert "not found" in res.message
    assert http.calls == []


@pytest.mark.asyncio
async def test_a_traversal_path_is_refused(ws, monkeypatch):
    http = _install_aiohttp(monkeypatch, [])
    res = await H.handle_speaker_identify(
        SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="../../etc/passwd")
    )
    assert res.status == "FAILURE"
    assert http.calls == []


@pytest.mark.asyncio
async def test_a_non_audio_file_is_refused_with_the_accepted_list(ws, monkeypatch):
    _write(ws, "notes.txt", b"hello")
    http = _install_aiohttp(monkeypatch, [])
    res = await H.handle_speaker_identify(
        SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="notes.txt")
    )
    assert res.status == "FAILURE"
    assert ".wav" in res.message
    assert http.calls == []


@pytest.mark.asyncio
async def test_an_unconfigured_audio_backend_says_so(ws, monkeypatch):
    """ALPACA_AUDIO_URL has no default on purpose, so the failure mode is a
    hostname that does not resolve - except when it was never set at all."""
    _write(ws, "clip.wav")
    monkeypatch.setattr(H, "ALPACA_AUDIO_URL", "")
    http = _install_aiohttp(monkeypatch, [])
    res = await H.handle_speaker_identify(
        SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="clip.wav")
    )
    assert res.status == "FAILURE"
    assert "ALPACA_AUDIO_URL" in res.message
    assert http.calls == []


# ---------------------------------------------------------------- list_voices


@pytest.mark.asyncio
async def test_list_voices_flattens_both_halves(monkeypatch):
    payload = {
        "saved_profiles": [{"id": "ada-abc", "name": "Ada", "engine": "openvoice-v2"}],
        "roster": [
            {"pair_id": "duo_warm", "slot": "a", "name": "Ada", "voice": "af_nicole", "gender": "f", "source_gender": "f"},
            {"pair_id": "duo_warm", "slot": "b", "name": "Rowan", "voice": "am_michael", "gender": "m", "source_gender": "m"},
        ],
    }
    _install_aiohttp(monkeypatch, [_Resp(200, payload)])
    res = await H.handle_list_voices(ListVoicesRequest(user_context=UC))
    assert res.status == "SUCCESS"
    assert res.detail["saved_profiles"][0]["name"] == "Ada"
    assert [h["name"] for h in res.detail["hosts"]] == ["Ada", "Rowan"]
    assert res.detail["hosts"][0]["source_gender"] == "f", "the caller needs both sides of the gender match"


@pytest.mark.asyncio
async def test_list_voices_can_be_restricted_to_one_pair(monkeypatch):
    roster = [
        {"pair_id": "duo_warm", "slot": "a", "name": "Ada"},
        {"pair_id": "duo_deep", "slot": "a", "name": "Vera"},
    ]
    _install_aiohttp(monkeypatch, [_Resp(200, {"saved_profiles": [], "roster": roster})])
    res = await H.handle_list_voices(ListVoicesRequest(user_context=UC, pair_id="duo_deep"))
    assert [h["name"] for h in res.detail["hosts"]] == ["Vera"]


@pytest.mark.asyncio
async def test_list_voices_survives_a_junk_roster_row(monkeypatch):
    _install_aiohttp(monkeypatch, [_Resp(200, {"saved_profiles": [None], "roster": [None, {"name": "Ada"}]})])
    res = await H.handle_list_voices(ListVoicesRequest(user_context=UC))
    assert res.status == "SUCCESS"
    assert res.detail["hosts"] == [{"pair_id": None, "slot": None, "name": "Ada", "voice": None, "role": None, "gender": None, "source_gender": None, "clone": None}]


@pytest.mark.asyncio
async def test_list_voices_reports_a_dashboard_failure(monkeypatch):
    _install_aiohttp(monkeypatch, [_Resp(502, None, text="audio server down")])
    res = await H.handle_list_voices(ListVoicesRequest(user_context=UC))
    assert res.status == "FAILURE"
    assert "502" in res.message


# ----------------------------------------------------------------- transport


def test_every_handler_returns_rather_than_raises(monkeypatch):
    """A transport blow-up must not become a 500 on the mission. The handlers
    promise an ExecutionResult for every outcome, so the tests can check that
    by making aiohttp itself explode."""
    module = types.ModuleType("aiohttp")

    def boom(*a, **k):
        raise RuntimeError("connection reset")

    module.ClientSession = boom
    module.ClientTimeout = lambda **k: None
    monkeypatch.setitem(sys.modules, "aiohttp", module)

    import asyncio

    async def go():
        out = [await H.handle_list_voices(ListVoicesRequest(user_context=UC))]
        out.append(await H.handle_podcast_render(_render_req()))
        out.append(
            await H.handle_speaker_identify(
                SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="a.wav")
            )
        )
        return out

    for res in asyncio.run(go()):
        assert res.status == "FAILURE", res.message
        assert "connection reset" in res.message


def test_the_routes_are_registered():
    """The handler is only reachable if main.py declares the route with the same
    name the tool registry points at."""
    source = (Path(H.__file__).resolve().parents[1] / "main.py").read_text()
    for path in ("/execute/podcast_render", "/execute/speaker_identify", "/execute/list_voices"):
        assert f'@app.post("{path}"' in source, f"{path} is not registered on the execution service"


# ------------------------------------------------- regressions from the review
#
# Each of these raised out of the handler, escaped the FAILURE contract the
# module docstring promises, or reported a value in the wrong form. The
# workspace-write one is the serious one: it is the only aiohttp call that runs
# AFTER a successful 30-minute render, so an exception there discarded a finished
# episode and the model re-ran the job.


class _RaisingSession:
    """A ClientSession whose *Nth* session raises, so the guarded first call can
    succeed and the unguarded later one fail.

    `test_every_handler_returns_rather_than_raises` faked a session that raises
    outright, which fires on the first call - the render, which is guarded - so it
    never reached the workspace write. That is how the gap survived.
    """

    def __init__(self, fail_on=1, responses=()):
        self.sessions = 0
        self._fail_on = fail_on
        self._queue = list(responses)
        self.calls = []

    def __call__(self, *a, **k):
        self.sessions += 1
        if self.sessions == self._fail_on:
            return self._raising()
        return _Client(self._queue)

    @staticmethod
    def _raising():
        client = _Client([])
        client._next = lambda: (_ for _ in ()).throw(OSError("Cannot connect to host workspace_runtime:8000"))
        return client


def _install_failing_aiohttp(monkeypatch, fail_on, responses=()):
    fake = _RaisingSession(fail_on=fail_on, responses=responses)
    module = types.ModuleType("aiohttp")
    module.ClientSession = fake
    module.ClientTimeout = lambda **k: ("timeout", k)
    monkeypatch.setitem(sys.modules, "aiohttp", module)
    return fake


@pytest.mark.asyncio
async def test_a_workspace_save_that_raises_still_reports_the_render(ws, monkeypatch):
    """The render succeeded. An exception escaping the save turned a finished
    episode into a 500 whose message contained no trace of the audio, and the
    mission re-ran a half-hour job to produce the same file."""
    _install_failing_aiohttp(monkeypatch, fail_on=2, responses=[_Resp(200, {"data_uri": "data:audio/wav;base64,QUJD"})])
    res = await H.handle_podcast_render(_render_req())
    assert res.status == "FAILURE"
    # "rendered but" is the distinction: the script was fine, the save was not.
    assert "rendered but" in res.message
    assert "workspace save failed" in res.message


@pytest.mark.asyncio
async def test_a_failed_save_does_not_lose_the_mix_statistics(ws, monkeypatch):
    _install_failing_aiohttp(
        monkeypatch,
        fail_on=2,
        responses=[_Resp(200, {"data_uri": "data:audio/wav;base64,QUJD", "duration_s": 61.0, "turn_count": 2})],
    )
    res = await H.handle_podcast_render(_render_req())
    assert res.detail["output_path"] == "podcast.wav"


@pytest.mark.asyncio
async def test_an_unreadable_clip_returns_a_failure_rather_than_raising(ws, monkeypatch):
    """`os.path.isfile()` passes on a mode-000 file, and the open() that follows
    raised PermissionError straight out of the handler."""
    locked = ws / "locked.wav"
    locked.write_bytes(b"RIFF" + b"\0" * 64)
    locked.chmod(0o000)
    try:
        _install_aiohttp(monkeypatch, [_Resp(200, {})])
        res = await H.handle_speaker_identify(SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="locked.wav"))
    finally:
        locked.chmod(0o644)
    assert res.status == "FAILURE"
    assert "cannot read" in res.message
    assert "locked.wav" in res.message


@pytest.mark.asyncio
async def test_the_runner_up_is_named_not_identified_by_hash(ws, monkeypatch):
    """`voice_clone.identify` returns `runner_up` as a profile id, so the message
    addressed the model as "b81e0d55aa02" while holding the name in `candidates`.
    The existing test mocked a name the server never produces."""
    (ws / "clip.wav").write_bytes(b"RIFF" + b"\0" * 64)
    payload = {
        "matched": "ada-1",
        "matched_name": "Ada",
        "score": 0.9137,
        "runner_up": "b81e0d55aa02",
        "candidates": [{"id": "ada-1", "name": "Ada", "score": 0.9137}, {"id": "b81e0d55aa02", "name": "Rowan", "score": 0.7992}],
    }
    _install_aiohttp(monkeypatch, [_Resp(200, payload)])
    res = await H.handle_speaker_identify(SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="clip.wav"))
    assert res.status == "SUCCESS"
    assert "next closest is Rowan" in res.message
    assert "b81e0d55aa02" not in res.message
    # the raw id is still in detail, where a caller can join on it
    assert res.detail["runner_up"] == "b81e0d55aa02"


@pytest.mark.asyncio
async def test_an_unknown_runner_up_id_falls_back_to_printing_it(ws, monkeypatch):
    """Defensive: a server that returns an id absent from candidates must not
    produce "next closest is None"."""
    (ws / "clip.wav").write_bytes(b"RIFF" + b"\0" * 64)
    payload = {"matched_name": "Ada", "score": 0.9, "runner_up": "gone", "candidates": [{"id": "ada-1", "name": "Ada", "score": 0.9}]}
    _install_aiohttp(monkeypatch, [_Resp(200, payload)])
    res = await H.handle_speaker_identify(SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="clip.wav"))
    assert "next closest is gone" in res.message


@pytest.mark.parametrize(
    "upstream,expected_fragment",
    [
        ("not enough speech to build a voice embedding", "not enough speech"),
        ("recording is larger than 25 MB", "25 MB"),
        ("could not decode the recording: moov atom not found", "moov atom"),
    ],
)
@pytest.mark.asyncio
async def test_a_422_reports_what_the_server_said(ws, monkeypatch, upstream, expected_fragment):
    """The audio server maps every ValueError to 422, and "not enough speech" is
    only one of the three. Leading with the wrong diagnosis sends a model looking
    in the wrong place - an oversize clip is a file-size problem, not a speech one.
    """
    (ws / "clip.wav").write_bytes(b"RIFF" + b"\0" * 64)
    _install_aiohttp(monkeypatch, [_Resp(422, None, text=upstream)])
    res = await H.handle_speaker_identify(SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="clip.wav"))
    assert res.status == "FAILURE"
    assert expected_fragment in res.message
    assert "not enough speech in the clip" not in res.message


@pytest.mark.asyncio
async def test_an_oversize_clip_is_refused_before_it_is_uploaded(ws, monkeypatch):
    """The handler read the whole file and base64-encoded it (+33%) only for the
    audio server to reject it on a limit the handler already knows."""
    big = ws / "big.wav"
    big.write_bytes(b"\0" * 16)
    monkeypatch.setattr(H, "_MAX_CLIP_BYTES", 8)
    http = _install_aiohttp(monkeypatch, [_Resp(200, {})])
    res = await H.handle_speaker_identify(SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="big.wav"))
    assert res.status == "FAILURE"
    assert "MB" in res.message
    assert http.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("output_path", ["README.md", "services/gateway/main.py", "podcast.txt", "noextension"])
async def test_a_non_audio_output_path_is_refused(ws, monkeypatch, output_path):
    """Contained to the workspace, so not an escape - but
    `output_path: "services/gateway/main.py"` returned SUCCESS and left a Python
    file full of WAV bytes. An audio tool writing a non-audio file is always a
    mistake, so refuse it before spending the render."""
    http = _install_aiohttp(monkeypatch, [_Resp(200, {"data_uri": "data:audio/wav;base64,QUJD"})] * 2)
    res = await H.handle_podcast_render(_render_req(output_path=output_path))
    assert res.status == "FAILURE"
    assert "audio" in res.message
    assert http.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("output_path", ["podcast.wav", "episodes/s01.mp3", "S02.WAV", "clip.webm", "a.opus"])
async def test_an_audio_output_path_in_any_accepted_container_is_allowed(ws, monkeypatch, output_path):
    http = _install_aiohttp(monkeypatch, [_Resp(200, {"data_uri": "data:audio/wav;base64,QUJD"})] * 2)
    res = await H.handle_podcast_render(_render_req(output_path=output_path))
    assert res.status == "SUCCESS", res.message
    assert http.calls[1][2]["json"]["relative_path"] == output_path


@pytest.mark.asyncio
async def test_a_webm_clip_is_accepted(ws, monkeypatch):
    """A browser MediaRecorder produces .webm by default, so the commonest real
    doorbell clip was the one hard-refused."""
    (ws / "doorbell.webm").write_bytes(b"\x1a\x45\xdf\xa3" + b"\0" * 64)
    _install_aiohttp(monkeypatch, [_Resp(200, {"matched_name": None, "candidates": []})])
    res = await H.handle_speaker_identify(SpeakerIdentifyRequest(workspace_id="w1", user_context=UC, audio_path="doorbell.webm"))
    assert res.status == "SUCCESS"
    assert "audio server returned" not in res.message


@pytest.mark.asyncio
async def test_a_junk_roster_row_does_not_break_a_pair_filtered_list(ws, monkeypatch):
    """The pair_id filter was the one place a roster row was touched before the
    isinstance guard three lines below it."""
    payload = {
        "saved_profiles": [{"id": "p1", "name": "Ada", "engine": "openvoice-v2"}],
        "roster": [None, "junk", {"pair_id": "duo_deep", "slot": "a", "name": "Vera", "voice": "af_heart"}],
    }
    _install_aiohttp(monkeypatch, [_Resp(200, payload)])
    res = await H.handle_list_voices(ListVoicesRequest(workspace_id="w1", user_context=UC, pair_id="duo_deep"))
    assert res.status == "SUCCESS"
    assert [h["name"] for h in res.detail["hosts"]] == ["Vera"]
    assert res.detail["saved_profiles"][0]["id"] == "p1"


def test_the_audio_suffix_list_covers_what_the_upstream_decoder_accepts():
    """Mirrors voice_clone.decode_to_22k's documented containers. .webm is here
    because MediaRecorder produces it by default."""
    for suffix in (".wav", ".mp3", ".m4a", ".m4b", ".ogg", ".oga", ".opus", ".webm", ".flac", ".aac", ".aiff", ".amr"):
        assert suffix in H._AUDIO_SUFFIXES, suffix


def test_the_clip_cap_matches_the_upstream_upload_cap():
    assert H._MAX_CLIP_BYTES == 25 * 1024 * 1024


def test_the_dispatch_timeout_covers_every_handler_budget():
    """The client-side ceiling must exceed what the handler waits for, or the
    model is told the job failed while it is still running. See the same check in
    test_podcast_tools.py, which reads the ladder from agent_loop."""
    assert H._RENDER_TIMEOUT_S > 120.0
    assert H._RENDER_TIMEOUT_S <= 3600.0  # a mission cannot outlast this
