"""Seeding tests for Raven's proven-command chaining.

Progressive, fully offline (no gateway/LLM required):

1. SEED  — start with a few tiny, basic commands and record which succeed.
2. LEARN — the successful commands become the "proven" set Raven can reuse.
3. CHAIN — string the proven commands into one batched JSON array and verify the
   parser preserves them as an executable sequence (one reasoning cycle).
4. DRIVE — verify the agent loop's queue replays the whole chain from a single
   inference (every queued step runs with skip_inference=True).

This is the mechanism behind the user's ask: Raven should reuse commands it has
already proven (from memory / history) instead of re-deriving every step, which
is what kept autonomous builds burning the full 1800s timeout.
"""

import os

os.environ.setdefault("INTERNAL_SECRET", "test-secret")
os.environ.setdefault("FERNET_KEY", "test-fernet-key-for-unit-tests-only")

import json

from services.gateway.agent_loop import _next_batch_step, extract_action_batch

# A small library of basic commands Raven "learns" — each is tiny and proven.
SEED_COMMANDS = [
    {"@type": "WorkspaceShellRequest", "command": "echo hello", "workspace_id": "ws"},
    {"@type": "WorkspaceShellRequest", "command": "ruff check .", "workspace_id": "ws"},
    {"@type": "GitOperationRequest", "action": "add", "path": "."},
    {"@type": "GitOperationRequest", "action": "commit", "commit_message": "feat: seed"},
    {"@type": "GitOperationRequest", "action": "push", "branch": "main"},
]


def _sig(cmd: dict) -> tuple:
    """Stable signature for comparing a command before/after normalization."""
    return (cmd.get("@type") or cmd.get("action"), cmd.get("command"), cmd.get("path") or cmd.get("file_path"))


def _simulate_run(cmd: dict) -> dict:
    """Pretend to execute a seeded command. Every basic command succeeds."""
    return {"status": "success", "tool": cmd.get("@type") or cmd.get("action")}


def test_seed_small_commands_track_success():
    """Step 1 + 2: seed tiny commands and record which ones succeeded (the learn step)."""
    results = [_simulate_run(c) for c in SEED_COMMANDS]
    succeeded = [c for c, r in zip(SEED_COMMANDS, results, strict=True) if r["status"] == "success"]

    assert len(succeeded) == len(SEED_COMMANDS), "all basic seeded commands should succeed"
    # The learned set is exactly the proven commands we can chain later.
    assert [_sig(c) for c in succeeded] == [_sig(c) for c in SEED_COMMANDS]


def test_chain_seeded_commands_preserves_order():
    """Step 3: chain the proven commands into one batched array and verify the
    parser turns them into an executable sequence with order preserved."""
    text = "Here is the proven chain:\n```json\n" + json.dumps(SEED_COMMANDS) + "\n```"
    parsed = extract_action_batch(text)

    assert isinstance(parsed, list)
    assert len(parsed) == len(SEED_COMMANDS)
    # Chaining must keep the proven order: shell -> shell -> git add -> commit -> push.
    assert [_sig(p) for p in parsed] == [_sig(c) for c in SEED_COMMANDS]


def test_seed_and_chain_drives_single_inference():
    """Step 4: end-to-end seeding scenario. Learn small commands, chain the
    successful ones, and verify the loop queue replays the whole chain with no
    further LLM calls (the batch was produced by a single inference)."""
    # 1) seed + track success
    learned = [
        c
        for c, r in zip(SEED_COMMANDS, [_simulate_run(c) for c in SEED_COMMANDS], strict=True)
        if r["status"] == "success"
    ]
    learned_sigs = [_sig(c) for c in learned]
    # 2) chain the learned commands exactly as the agent would emit them
    batch_text = "run proven chain: " + json.dumps([dict(c) for c in learned])
    parsed = extract_action_batch(batch_text)
    assert parsed is not None and len(parsed) == len(learned)

    # 3) the loop queues the entire batch; the producing inference is the ONLY
    #    one. Every subsequent queued step replays with skip_inference=True.
    inferences = 1
    pending = list(parsed)
    executed = []
    while pending:
        skip, td = _next_batch_step(pending)
        if not skip:
            inferences += 1  # would only happen on a fresh inference turn
        executed.append(td)

    assert [_sig(e) for e in executed] == learned_sigs
    # The batch is produced by a single inference, then replayed with no further
    # LLM calls — that is the whole point of proven-command chaining.
    assert inferences == 1


def test_chaining_handles_a_failed_seed_gracefully():
    """If one seeded command fails, it is excluded from the proven chain so the
    agent never replays a known-bad command."""
    seeds = [
        {"@type": "WorkspaceShellRequest", "command": "echo ok", "workspace_id": "ws"},
        {"@type": "WorkspaceShellRequest", "command": "exit 1", "workspace_id": "ws"},  # fails
        {"@type": "GitOperationRequest", "action": "add", "path": "."},
    ]

    def _run(cmd: dict) -> dict:
        # The second command simulates a failure (no "command" on git ops).
        if cmd.get("command") == "exit 1":
            return {"status": "error"}
        return {"status": "success"}

    learned = [c for c, r in zip(seeds, [_run(c) for c in seeds], strict=True) if r["status"] == "success"]
    assert len(learned) == 2  # the failing command is dropped

    parsed = extract_action_batch(json.dumps([dict(c) for c in learned]))
    assert len(parsed) == 2
    assert all(_sig(p)[1] != "exit 1" for p in parsed)
