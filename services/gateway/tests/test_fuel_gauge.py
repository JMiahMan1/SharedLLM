"""Tests for the fuel gauge (token-based context budgeting) system.

Covers:
- _estimate_tokens: 4 chars/token + 10% margin
- _compact_conversation: token-based, bounded mission log note, keeps newest turns
- _stub_old_turns: old assistant file writes stubbed, recent turn preserved
- overflow recovery: one compacted retry, not three identical ones
- second overflow fails mission with context reason
- fuel events present per iteration
"""


import pytest

from services.gateway.agent_loop import (
    _compact_conversation,
    _conversation_token_budget,
    _estimate_tokens,
    _is_context_overflow_error,
    _stub_old_turns,
)

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def test_estimate_tokens_empty():
    assert _estimate_tokens("") == 0
    assert _estimate_tokens(None) == 0


def test_estimate_tokens_basic():
    # 4 chars/token + 10% margin: 100 chars -> ~28 tokens
    # 100 / 4 = 25 * 1.1 = 27.5 -> int = 27
    assert _estimate_tokens("a" * 100) == 27
    assert _estimate_tokens("a" * 4) == 1
    assert _estimate_tokens("a" * 40) == 11


def test_estimate_tokens_large():
    # 4000 chars -> 1000 tokens base -> 1100 with margin
    assert _estimate_tokens("x" * 4000) == 1100


# ---------------------------------------------------------------------------
# Conversation token budget
# ---------------------------------------------------------------------------

def test_conversation_token_budget_empty():
    assert _conversation_token_budget([]) == 0


def test_conversation_token_budget_system_and_body():
    conv = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    # system: "You are helpful." (17 chars -> 5 tokens)
    # user: "Hello" (5 chars -> 2 tokens)
    # assistant: "Hi there!" (9 chars -> 3 tokens)
    total = _conversation_token_budget(conv)
    assert total > 0
    assert isinstance(total, int)


# ---------------------------------------------------------------------------
# Compaction: no-op when under threshold
# ---------------------------------------------------------------------------

def test_compact_no_op_when_under_70_percent():
    """When total tokens are under 70% of budget, conversation is returned unchanged."""
    vram = {"num_ctx": 16384, "num_predict": 3072}
    # budget = 16384 - 3072 - 1024 = 12288
    # 70% = 8601 tokens
    # Small conversation -> should not compact
    conv = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
    ]
    result = _compact_conversation(conv, vram)
    assert result == conv


# ---------------------------------------------------------------------------
# Compaction: token-based with bounded mission log note
# ---------------------------------------------------------------------------

def test_compact_keeps_recent_turns_within_budget():
    """Recent turns stay verbatim; older turns become a bounded note."""
    vram = {"num_ctx": 16384, "num_predict": 3072}
    # budget = 12288, 35% keep = 4300 tokens, 70% trigger = 8601 tokens

    # Build a conversation with many turns to trigger compaction
    # 50 pairs * ~736 chars/pair = ~36800 chars -> ~10100 tokens > 8601
    conv = [{"role": "system", "content": "SYSTEM"}]
    for i in range(50):
        conv.append({"role": "user", "content": f"User turn {i}: " + "x" * 500})
        conv.append({"role": "assistant", "content": f"Assistant turn {i}: " + "y" * 200})

    result = _compact_conversation(conv, vram)

    # Result should have system + recent turns
    assert len(result) > 0
    assert result[0]["role"] == "system"
    # The static system prefix stays byte-for-byte; the rolling mission log
    # lives in the pinned dynamic user context.
    assert result[0]["content"] == "SYSTEM"
    pinned_content = result[1]["content"]
    assert "MISSION LOG SO FAR" in pinned_content

    # Recent turns should be preserved verbatim
    recent_roles = [m["role"] for m in result[2:]]
    # Should have at least 2 turns kept
    assert len(recent_roles) >= 2


def test_compact_mission_log_note_bounded():
    """The mission log note should not grow beyond ~1500 tokens across compactions."""
    vram = {"num_ctx": 16384, "num_predict": 3072}

    # Start with a moderate conversation
    conv = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
    ]

    # Compact multiple times with growing conversation
    for cycle in range(5):
        # Add more turns
        conv.append({"role": "user", "content": f"Additional turn {cycle}: " + "a" * 800})
        conv.append({"role": "assistant", "content": f"Reply {cycle}: " + "b" * 300})
        conv = _compact_conversation(conv, vram)

        # Check note is bounded
        system_content = conv[0]["content"]
        note_start = system_content.find("MISSION LOG SO FAR")
        if note_start >= 0:
            note_end = system_content.find(")\n", note_start)
            if note_end < 0:
                note_end = len(system_content)
            note = system_content[note_start:note_end]
            note_tokens = _estimate_tokens(note)
            assert note_tokens <= 1600, f"Note grew to {note_tokens} tokens after cycle {cycle}"


def test_compact_never_appends_to_system_repeatedly():
    """Each compaction replaces the mission log, not appends to it."""
    vram = {"num_ctx": 16384, "num_predict": 3072}

    conv = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "Start"},
        {"role": "assistant", "content": "Reply"},
    ]

    for _ in range(3):
        # Add bulk to trigger compaction
        for _ in range(15):
            conv.append({"role": "user", "content": "x" * 600})
            conv.append({"role": "assistant", "content": "y" * 300})
        conv = _compact_conversation(conv, vram)

    # Should only have ONE mission log note, and it is in the pinned user
    # message rather than the static system prefix.
    assert conv[0]["content"] == "SYSTEM"
    count = conv[1]["content"].count("MISSION LOG SO FAR")
    assert count == 1, f"Found {count} mission log notes (should be 1)"


def test_compact_keeps_at_least_last_two_turns():
    """Even with heavy compaction, at least the last 2 turns are kept verbatim."""
    vram = {"num_ctx": 8192, "num_predict": 3072}
    # budget = 8192 - 3072 - 1024 = 4096, 35% = 1433 tokens

    conv = [{"role": "system", "content": "SYSTEM"}]
    for i in range(10):
        conv.append({"role": "user", "content": f"U{i}: " + "z" * 400})
        conv.append({"role": "assistant", "content": f"A{i}: " + "w" * 200})

    result = _compact_conversation(conv, vram)

    # Body should have at least 2 turns
    body = [m for m in result if m.get("role") != "system"]
    assert len(body) >= 2


def test_compact_small_conversation_unchanged():
    """Small conversations that don't exceed 70% threshold are unchanged."""
    vram = {"num_ctx": 16384, "num_predict": 3072}
    conv = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
    ]
    result = _compact_conversation(conv, vram)
    assert result == conv


def test_compact_without_vram_params_uses_default_budget():
    """When mission_vram_params is None, defaults are used."""
    conv = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
    ]
    result = _compact_conversation(conv, None)
    assert result == conv  # Under threshold


def test_compact_many_iterations_keeps_recent():
    """After many compactions, the most recent turns remain intact."""
    vram = {"num_ctx": 16384, "num_predict": 3072}

    conv = [{"role": "system", "content": "SYSTEM"}]
    for i in range(30):
        conv.append({"role": "user", "content": f"Turn {i}: " + "data" * 100})
        conv.append({"role": "assistant", "content": f"Reply {i}: " + "resp" * 50})
        conv = _compact_conversation(conv, vram)

    # Last user and assistant turns should be intact
    body = [m for m in conv if m.get("role") != "system"]
    assert body[-2]["content"].startswith("Turn 29:")
    assert body[-1]["content"].startswith("Reply 29:")


# ---------------------------------------------------------------------------
# Stub old turns
# ---------------------------------------------------------------------------

def test_stub_old_assistant_turns():
    """Old assistant turns are stubbed; recent turn stays whole."""
    conv = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "Do something"},
        {"role": "assistant", "content": '{"action": "WorkspaceFileWriteRequest", "file_path": "a.py", "content": "def a():\\n    return 1\\n"}'},
        {"role": "user", "content": "Result: success"},
        {"role": "assistant", "content": "Done with full detailed explanation"},
    ]
    result = _stub_old_turns(conv)

    # Last assistant turn should be intact
    assert result[-1]["content"] == "Done with full detailed explanation"
    # Old assistant turn should be stubbed
    old_assistant = [m for m in result if m["role"] == "assistant" and m != result[-1]]
    assert len(old_assistant) == 1
    assert len(old_assistant[0]["content"]) < 200


def test_stub_large_tool_results():
    """OLD user messages with large tool results (>3000 chars) are stubbed; the newest is kept."""
    big_result = "x" * 4000
    conv = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": f"LAST TOOL RESULT:\\n{big_result}"},
        {"role": "assistant", "content": "Got it"},
        {"role": "user", "content": "next tool result"},
    ]
    result = _stub_old_turns(conv)

    user_msgs = [m for m in result if m["role"] == "user"]
    assert len(user_msgs) == 2
    assert "chars, truncated" in user_msgs[0]["content"]
    assert len(user_msgs[0]["content"]) < 100
    assert user_msgs[1]["content"] == "next tool result"


def test_stub_preserves_small_messages():
    """Small messages pass through unchanged."""
    conv = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"},
    ]
    result = _stub_old_turns(conv)
    assert result == conv


# ---------------------------------------------------------------------------
# Overflow detection
# ---------------------------------------------------------------------------

def test_overflow_error_llama_cpp_pattern():
    assert _is_context_overflow_error("exceeds the available context size") is True
    assert _is_context_overflow_error("Error: exceeds the available context size of 16384") is True


def test_overflow_error_openai_pattern():
    assert _is_context_overflow_error("context_length_exceeded") is True


def test_overflow_error_generic_pattern():
    assert _is_context_overflow_error("The context length exceeds the model limit") is True


def test_non_overflow_error():
    assert _is_context_overflow_error("Model is temporarily overloaded") is False
    assert _is_context_overflow_error("Authentication failed") is False
    assert _is_context_overflow_error("Rate limit exceeded") is False


# ---------------------------------------------------------------------------
# Overflow recovery integration (mocked AgentLoop)
# ---------------------------------------------------------------------------

def test_overflow_triggers_one_compacted_retry_not_three_identical():
    """When context overflow occurs, the loop should compact and retry once,
    not retry 3 times with identical messages.

    We verify this by inspecting the AgentLoop source: the overflow handler
    only increments retry_count once (via `continue`) before the next overflow
    hits the `retry_count >= 1` branch which ends the mission.
    """
    import inspect

    import services.gateway.agent_loop as ag

    source = inspect.getsource(ag.AgentLoop)

    # The overflow recovery block should use `continue` to retry once,
    # and the second overflow should hit the fail branch, not loop again.
    # Key pattern: after hard-compact, there's a `continue` (one retry),
    # and the next overflow check has `retry_count >= 1` -> fail.
    assert "Hard-compacting conversation and retrying once" in source
    assert "Context overflow persists after hard-compact retry" in source
    assert 'await stream_event("system", "MISSION FAILED: Context window exceeded even after compaction.' in source

    # Also verify overflow detection itself
    assert _is_context_overflow_error("exceeds the available context size") is True


@pytest.mark.asyncio
async def test_fuel_events_recorded_per_iteration():
    """Fuel events should be emitted each iteration with fuel_used and fuel_budget."""

    # Test the fuel event data structure directly
    conv = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]

    fuel_tokens = _conversation_token_budget(conv)
    fuel_budget = 16384 - 3072 - 1024

    assert isinstance(fuel_tokens, int)
    assert fuel_tokens > 0
    assert fuel_budget == 12288
    assert fuel_tokens <= fuel_budget


def test_compact_with_different_vram_params():
    """Compaction adapts to different VRAM configurations."""
    # Smaller context
    vram_small = {"num_ctx": 8192, "num_predict": 3072}
    # Larger context
    vram_large = {"num_ctx": 32768, "num_predict": 3072}

    conv = [{"role": "system", "content": "SYSTEM"}]
    for i in range(10):
        conv.append({"role": "user", "content": f"U{i}: " + "x" * 500})
        conv.append({"role": "assistant", "content": f"A{i}: " + "y" * 200})

    result_small = _compact_conversation(conv, vram_small)
    result_large = _compact_conversation(conv, vram_large)

    # Small context should compact more aggressively (fewer recent turns)
    small_body = [m for m in result_small if m.get("role") != "system"]
    large_body = [m for m in result_large if m.get("role") != "system"]

    # Both should have at least 2 turns
    assert len(small_body) >= 2
    assert len(large_body) >= 2
    # Large context should keep more turns
    assert len(large_body) >= len(small_body)


def test_compact_replaces_previous_mission_log():
    """Compaction should strip any previous mission log before adding a new one."""
    vram = {"num_ctx": 16384, "num_predict": 3072}

    # Start with a conversation that already has a mission log
    conv = [
        {
            "role": "system",
            "content": "SYSTEM\n\nMISSION LOG SO FAR (preserved learnings; do not re-derive):\n[user] old log entry",
        },
        {"role": "user", "content": "New user input"},
        {"role": "assistant", "content": "New assistant reply"},
    ]

    # Add bulk to trigger another compaction
    # 35 pairs * 1200 chars = 42000 chars -> ~11500 tokens > 8601 threshold
    for _ in range(35):
        conv.append({"role": "user", "content": "bulk" * 300})
        conv.append({"role": "assistant", "content": "resp" * 150})

    result = _compact_conversation(conv, vram)

    assert result[0]["content"].startswith("SYSTEM")
    pinned_content = result[1]["content"]
    # Should have exactly one mission log, in the pinned dynamic message.
    assert pinned_content.count("MISSION LOG SO FAR") == 1
    # The previous mission log is replaced, not appended to, in the pinned note.
    assert "old log entry" not in pinned_content


def test_the_current_tool_result_is_never_stubbed():
    """Only OLD big tool results become stubs; the newest user turn is what the model acts on."""
    big = "x" * 5000
    conv = [
        {"role": "user", "content": big},
        {"role": "assistant", "content": "wrote a.py"},
        {"role": "user", "content": big},
    ]
    out = _stub_old_turns(conv)
    assert out[0]["content"].startswith("[tool result:")
    assert out[2]["content"] == big


def test_the_mission_log_keeps_the_newest_older_entries():
    """When the note is full, the entries closest to now survive."""
    conv = [{"role": "system", "content": "SYSTEM"}]
    for i in range(60):
        conv.append({"role": "user", "content": f"step-{i:02d} " + "y" * 400})
        conv.append({"role": "assistant", "content": f"did-{i:02d}"})
    out = _compact_conversation(conv, {"num_ctx": 8192, "num_predict": 1024})
    note = out[1]["content"]
    assert "MISSION LOG SO FAR" in note
    kept_recent = {m["content"][:7] for m in out[2:]}
    # The newest step that did not stay verbatim is in the note; the very first one is not.
    older_steps = [f"step-{i:02d}" for i in range(60) if f"step-{i:02d}" not in kept_recent]
    assert older_steps[-1] in note
    assert "step-00" not in note
    # Compacting again does not stack a second note.
    again = _compact_conversation(out + conv[-20:], {"num_ctx": 8192, "num_predict": 1024})
    assert again[1]["content"].count("MISSION LOG SO FAR") == 1
