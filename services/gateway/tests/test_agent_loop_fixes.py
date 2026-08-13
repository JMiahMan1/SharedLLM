
from services.gateway.agent_loop import (
    OllamaProvider,
    _has_valid_workspace_id,
    _next_batch_step,
    action_signature,
    build_adaptive_guidance,
    compose_timeout_partial_result,
    extract_action_batch,
    guidance_branch,
    is_verification_action,
    outcome_digest,
)


def test_timeout_partial_result_surfaces_real_progress_not_batch_placeholder():
    """Regression: a hard-timeout mission reported 'Partial result: [batch]
    TTSRequest' — the internal batch-continuation placeholder — because the raw
    model response was used directly. The timeout partial result must instead
    summarize what was actually accomplished (tool calls, artifacts, steps).
    """
    written = {"audiobook.mp3", "pray_day_00.wav"}
    verified = {"audiobook.mp3"}
    log = [
        "Step 3: audiobookregeneraterequest -> Success",
        "ITERATION 4: this is an internal nudge, not a result",
        "Step 4: ttsrequest -> Success",
    ]
    summary = compose_timeout_partial_result(2, log, written, verified)

    assert "[batch] TTSRequest" not in summary
    assert "2 tool call(s) completed across 3 logged step(s)" in summary
    assert "audiobook.mp3" in summary
    assert "pray_day_00.wav" in summary
    # Action-log nudges must be excluded from the user-facing summary.
    assert "internal nudge" not in summary
    assert "audiobookregeneraterequest -> Success" in summary


def test_timeout_partial_result_empty_state():
    """Empty mission state should still yield a readable, non-crashy summary."""
    summary = compose_timeout_partial_result(0, [], set(), set())
    assert "0 tool call(s) completed" in summary

    # Only nudge lines in the log -> no 'Recent actions' filler.
    summary2 = compose_timeout_partial_result(0, ["ITERATION 2: nudge"], set(), set())
    assert "Recent actions:" not in summary2


def test_ollama_sock_read_not_capped_at_60_for_large_context_prefill():
    """Regression: sock_read must NOT be a tiny fixed cap.

    The prompt-eval/prefill phase on a large num_ctx legitimately streams NO
    chunks for over a minute. The old ``min(total, 60)`` fired sock_read
    mid-prefill and triggered request-restart retries, which was the root
    cause of the 30s-quantised inference stalls (150006ms/240011ms). sock_read
    must be a large fraction of total (>= 180s) so it only catches a truly
    wedged stream, never a normal long prefill.
    """
    p = OllamaProvider("http://example:11434", timeout=600.0)
    assert p.timeout.sock_read is not None
    assert p.timeout.sock_read >= 180.0, "sock_read too small; would kill long prefills"
    assert p.timeout.sock_read >= 0.8 * 600.0
    assert p.timeout.total == 600.0

    # Even for a modest total, sock_read has a sane floor of 180s.
    p2 = OllamaProvider("http://example:11434", timeout=120.0)
    assert p2.timeout.sock_read >= 180.0

    # An explicit ClientTimeout is passed through untouched.
    import aiohttp

    explicit = aiohttp.ClientTimeout(total=42.0, sock_read=7.0)
    p3 = OllamaProvider("http://example:11434", timeout=explicit)
    assert p3.timeout.sock_read == 7.0
    assert p3.timeout.total == 42.0


def test_normalize_tool_rejects_malformed_file_write():
    """Regression: a truncated/bag-of-words file-write call must be REJECTED
    (return None), never normalized into an executable write to path ':'.

    Observed live (mission 2): the model emitted
      {"file_path":":", "envdiff":"/core.py", "content":":"}
    after a 2048-token truncation. The old code accepted it and executed a
    write to ':' — poisoning the workspace so the mission looped on garbage.
    """
    from services.gateway.agent_loop import _normalize_tool

    # bag-of-words stub with garbage file_path + stub content
    bag = {
        "file_path": ":",
        "envdiff": "/core.py",
        "content": ":",
        "core": "parsing",
        "and": "value[-1]",
    }
    assert _normalize_tool(bag) is None

    # content is a dict of words (not a string) -> reject
    dict_content = {"file_path": "envdiff/core.py", "content": {"foo": "bar"}}
    assert _normalize_tool(dict_content) is None

    # content too short -> reject
    short = {"file_path": "envdiff/core.py", "content": "x"}
    assert _normalize_tool(short) is None

    # empty / dot / slash file path -> reject
    for fp in ("", ".", "/", ":"):
        assert _normalize_tool({"file_path": fp, "content": "def f():\n    return 1\n"}) is None

    # a WELL-FORMED write is accepted
    good = _normalize_tool(
        {"file_path": "envdiff/core.py", "content": "class EnvDiff:\n    pass\n"}
    )
    assert good is not None
    assert good["action"] == "WorkspaceFileWriteRequest"
    assert good["file_path"] == "envdiff/core.py"
    assert "class EnvDiff" in good["content"]


def test_valid_structured_tool_gate():
    """The shared gate must accept valid calls and reject the live garbage shape."""
    from services.gateway.agent_loop import _valid_structured_tool

    # valid write
    assert _valid_structured_tool(
        {"action": "WorkspaceFileWriteRequest", "file_path": "envdiff/core.py",
         "content": "class EnvDiff:\n    pass\n"}
    ) is True

    # the exact bag-of-words garbage seen at mission 3 iters 10-12
    garbage = {
        "@type": "WorkspaceFileWriteRequest", "file_path": ":",
        "envdiff": ".core", "content": ":",
        "workspace_id": "raven-envdiff-run3", "type": ":",
        "action": "WorkspaceFileWriteRequest",
        "payload": {"file_path": ":", "content": ":"},
    }
    assert _valid_structured_tool(garbage) is False

    # read with a stub path is rejected
    assert _valid_structured_tool(
        {"action": "WorkspaceFileReadRequest", "file_path": ":"}
    ) is False

    # non-structured actions are never gated (shell/git pass through)
    assert _valid_structured_tool(
        {"action": "WorkspaceShellRequest", "command": "ls"}
    ) is True


def test_next_batch_step_logic():
    # Empty batch
    skip, tool = _next_batch_step([])
    assert skip is False
    assert tool is None

    # Non-empty batch
    batch = [{"action": "first"}, {"action": "second"}]
    skip, tool = _next_batch_step(batch)
    assert skip is True
    assert tool == {"action": "first"}
    assert len(batch) == 1
    assert batch[0] == {"action": "second"}

def test_is_verification_action():
    assert is_verification_action("workspacelintrequest", {}) is True
    assert is_verification_action("workspaceshellrequest", {"command": "ruff check ."}) is True
    assert is_verification_action("workspaceshellrequest", {"command": "pytest"}) is True
    assert is_verification_action("workspaceshellrequest", {"command": "python main.py"}) is False

def test_action_signature():
    assert action_signature("WorkspaceFileWriteRequest", {"file_path": "game.py"}) == "workspacefilewriterequest::'game.py'"
    assert action_signature("WorkspaceShellRequest", {"command": "ruff check"}) == "workspaceshellrequest::'ruff check'"

def test_outcome_digest():
    assert outcome_digest({"message": "Success!"}) == "Success!"
    assert outcome_digest("not a dict") == "na"
    assert outcome_digest({"detail": "Error occurred somewhere deep"}) == "Error occurred somewhere deep"


def test_has_valid_workspace_id():
    # Valid ids
    assert _has_valid_workspace_id("raven-proj") is True
    assert _has_valid_workspace_id("  ws-1  ") is True
    # Blank / unassigned -> must be treated as invalid so the guard fires and
    # we never send a workspace_shell with no id (execution service 400).
    assert _has_valid_workspace_id(None) is False
    assert _has_valid_workspace_id("") is False
    assert _has_valid_workspace_id("   ") is False
    assert _has_valid_workspace_id("\t\n") is False


def test_extract_action_batch_fenced_json_array():
    text = (
        "Here is my plan:\n"
        "```json\n"
        '[{"@type": "WorkspaceFileWriteRequest", "file_path": "a.py", "content": "def a():\n    return 1\n"},'
        ' {"@type": "WorkspaceFileWriteRequest", "file_path": "b.py", "content": "def b():\n    return 2\n"},'
        ' {"@type": "WorkspaceShellRequest", "command": "pytest -q"}]\n'
        "```\n"
    )
    batch = extract_action_batch(text)
    assert batch is not None
    assert len(batch) == 3
    # Each item normalized to carry an action / @type.
    assert all(b.get("action") or b.get("@type") for b in batch)


def test_extract_action_batch_bare_array():
    text = '[{"@type": "WorkspaceShellRequest", "command": "ls"}, {"@type": "WorkspaceShellRequest", "command": "pwd"}]'
    batch = extract_action_batch(text)
    assert batch is not None
    assert len(batch) == 2


def test_extract_action_batch_single_object_is_not_a_batch():
    # A lone object (not an array) is handled by the single-action extractor,
    # so the batch extractor must return None.
    text = '{"@type": "WorkspaceShellRequest", "command": "ls"}'
    assert extract_action_batch(text) is None


def test_extract_action_batch_prose_is_not_a_batch():
    # Plain prose with no JSON array yields no batch.
    assert extract_action_batch("First I will create the workspace, then write files.") is None


def test_extract_action_batch_real_file_content_with_newlines():
    # REGRESSION: real file content always contains literal newlines/tabs inside
    # the JSON string values. The batch extractor previously used strict json.loads
    # (no control-char repair), so ANY batch of real file writes silently returned
    # None and the loop fell back to a single tool call per turn. This masqueraded
    # as "the model refuses to batch" when the parser was actually dropping it.
    text = (
        "[ "
        '{"@type": "WorkspaceFileWriteRequest", "file_path": "core.py", '
        '"content": "import os\n\ndef main():\n\treturn os.getcwd()\n"}, '
        '{"@type": "WorkspaceFileWriteRequest", "file_path": "test_core.py", '
        '"content": "from core import main\n\ndef test_main():\n\tassert main()\n"} '
        "]"
    )
    batch = extract_action_batch(text)
    assert batch is not None, "batch with real newlines in content must parse"
    assert len(batch) == 2
    # Content must be preserved intact, including its newlines.
    assert "\n" in batch[0]["content"]
    assert batch[0]["file_path"] == "core.py"
    assert batch[1]["file_path"] == "test_core.py"


def test_extract_action_batch_fenced_with_newlines_and_tabs():
    # Same repair path, but wrapped in a ```json fence like the model emits.
    text = (
        "```json\n"
        "[ "
        '{"@type": "WorkspaceFileWriteRequest", "file_path": "m.py", '
        '"content": "def f():\n\treturn 1\n"}, '
        '{"@type": "WorkspaceShellRequest", "command": "python -m pytest -q"} '
        "]\n"
        "```"
    )
    batch = extract_action_batch(text)
    assert batch is not None
    assert len(batch) == 2
    assert batch[0]["file_path"] == "m.py"


def test_batch_drain_semantics_on_failure():
    # Simulate the loop's drain-on-failure invariant: when a batched step fails,
    # the remaining queued steps must be discarded so the model re-plans.
    pending = [{"action": "b"}, {"action": "c"}]
    # step failed -> drain
    step_failed = True
    if pending and step_failed:
        pending.clear()
    assert pending == []


def test_batch_continues_on_success():
    pending = [{"action": "b"}, {"action": "c"}]
    step_failed = False
    if pending and step_failed:
        pending.clear()
    # On success the queue is untouched (next step dequeued elsewhere).
    assert len(pending) == 2


def _guidance(**kw):
    base = dict(
        workspace_id="ws-1",
        files_written=2,
        last_status=None,
        elapsed_frac=0.1,
        repeating=False,
    )
    base.update(kw)
    return build_adaptive_guidance(**base)


def test_guidance_no_workspace_says_create_first():
    g = _guidance(workspace_id=None, files_written=0)
    assert "WorkspaceCreateRequest" in g
    # No workspace yet -> guidance should still push a batched setup chain,
    # consistent with the protocol (create -> repo -> settings in one array).
    assert "JSON ARRAY" in g


def test_guidance_pushes_batching_when_building():
    g = _guidance(workspace_id="ws-1", files_written=0)
    assert "JSON ARRAY" in g
    # No files yet -> explicitly tell it to batch the initial files.
    assert "written NO files yet" in g


def test_guidance_failure_takes_priority_over_batching():
    g = _guidance(last_status="ERROR")
    assert "FAILED" in g
    # Must not also spam the generic batch push when a failure needs attention.
    assert "JSON ARRAY" not in g


def test_guidance_lint_errors_treated_as_failure():
    g = _guidance(last_status="LINT_ERRORS")
    assert "FAILED" in g


def test_guidance_budget_warning_when_low_on_time():
    g = _guidance(elapsed_frac=0.85)
    assert "time budget" in g
    assert "PUSH" in g


def test_guidance_repeating_redirects():
    g = _guidance(repeating=True)
    assert "REPEATING" in g
    assert "RavenRecallRequest" in g


def _branch(**kw):
    base = dict(
        workspace_id="ws-1",
        last_status=None,
        elapsed_frac=0.1,
        repeating=False,
    )
    base.update(kw)
    return guidance_branch(**base)


def test_branch_tags_match_guidance_priority():
    # Failure beats everything.
    assert _branch(last_status="ERROR", repeating=True, elapsed_frac=0.9) == "fail"
    assert _branch(last_status="LINT_ERRORS") == "fail"
    # Repeat beats budget + phase.
    assert _branch(repeating=True, elapsed_frac=0.9) == "repeat"
    # Budget beats phase.
    assert _branch(elapsed_frac=0.7) == "budget"
    # No workspace yet -> create.
    assert _branch(workspace_id="") == "create_ws"
    assert _branch(workspace_id=None) == "create_ws"
    # Healthy building phase -> batch.
    assert _branch() == "batch"


def test_workspace_create_guard_blocks_assigned_workspace_missions():
    """Regression: chained children / follow-ups inherit the parent's workspace
    and MUST run there. The model must never be allowed to spawn a new workspace
    for an assigned-workspace mission (observed live: mission 17 created
    'raven-child-output' instead of using the assigned 'Test')."""
    from services.gateway.agent_loop import _workspace_create_guard_message

    # Assigned workspace + create attempt -> blocked with a redirect message.
    msg = _workspace_create_guard_message("Test", "workspacecreaterequest")
    assert msg is not None
    assert "Test" in msg
    assert "Do NOT call WorkspaceCreateRequest" in msg

    # Non-create actions are never blocked by this guard.
    assert _workspace_create_guard_message("Test", "workspacefilewriterequest") is None
    assert _workspace_create_guard_message("Test", "workspaceshellrequest") is None
    assert _workspace_create_guard_message("Test", "gitoperationrequest") is None

    # No assigned workspace -> create allowed (project missions acquire a sandbox).
    assert _workspace_create_guard_message(None, "workspacecreaterequest") is None
    assert _workspace_create_guard_message("", "workspacecreaterequest") is None
    assert _workspace_create_guard_message("   ", "workspacecreaterequest") is None
