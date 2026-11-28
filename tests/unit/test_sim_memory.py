"""The pure SIM policy: scopes, privacy, model families, lifecycle and budgets."""
from __future__ import annotations

from services.common.sim_memory import (
    HARNESS_CHAR_BUDGETS,
    SimProblem,
    format_sim_block,
    make_problem_id,
    normalize_model_family,
    scrub_system_sim_text,
    status_priority,
    summarize_history,
)


def test_model_families_are_stable_across_versions_and_provider_prefixes():
    assert normalize_model_family("kwaipilot-kat-coder-v2-5-dev-iq4-nl") == "kat-coder"
    assert normalize_model_family("qwen3-6-35b-a3b-instruct") == "qwen"
    assert normalize_model_family("openrouter/qwen3-6-35b:latest") == "qwen"
    assert normalize_model_family("inkling-2-mini") == "inkling"
    assert normalize_model_family("gemini-2.5-pro") == "gemini"
    assert normalize_model_family("claude-sonnet-4-5") == "claude"
    assert normalize_model_family("deepseek-v3.2") == "deepseek"
    assert normalize_model_family("meta-llama/Llama-3.3-70B") == "llama"
    assert normalize_model_family("") == "unknown"
    # Unknown versions of the same base still group together.
    assert normalize_model_family("acme-7b") == normalize_model_family("acme-13b")


def test_system_sim_scrubs_personal_data_secrets_and_paths():
    raw = (
        "Ask alice@example.com to retry with sk-abc1234567890 at "
        "/Users/alice/Code/private/secrets.py from 192.168.1.42 or +1 555-123-4567 "
        "using api_key=super-secret and Bearer abcdefghijklmnop"
    )
    clean = scrub_system_sim_text(raw)

    assert "alice@example.com" not in clean
    assert "sk-abc1234567890" not in clean
    assert "/Users/alice" not in clean
    assert "192.168.1.42" not in clean
    assert "555-123-4567" not in clean
    assert "super-secret" not in clean
    assert "abcdefghijklmnop" not in clean
    assert "[email]" in clean and "[path]" in clean and "[token]" in clean


def test_system_and_user_problem_ids_never_collide():
    system_id = make_problem_id(
        scope="system", user_id="default", harness="raven", category="tool", signature="same"
    )
    alice = make_problem_id(
        scope="user", user_id="alice", harness="raven", category="tool", signature="same"
    )
    bob = make_problem_id(
        scope="user", user_id="bob", harness="raven", category="tool", signature="same"
    )
    assert len({system_id, alice, bob}) == 3
    assert alice == make_problem_id(
        scope="user", user_id="Alice", harness="raven", category="tool", signature="same"
    )


def _problem(**over):
    base = dict(
        problem_id="sim-1",
        scope="system",
        user_id="default",
        harness="satellite",
        category="tool_recovery",
        status="candidate",
        rationale="A schema mistake is recoverable.",
        edit_or_rule="Use the field name the error names.",
        verified_models=[],
        regressed_models=[],
        requires_admin=False,
    )
    base.update(over)
    return base


def test_formatter_marks_scope_and_portability_and_respects_every_budget():
    items = [
        _problem(problem_id="p", status="portable", verified_models=["qwen", "claude"]),
        _problem(problem_id="v", scope="user", user_id="alice", status="verified", verified_models=["qwen"]),
        _problem(problem_id="o", status="verified", verified_models=["claude"]),
        _problem(problem_id="c", status="candidate"),
    ]
    for harness, budget in HARNESS_CHAR_BUDGETS.items():
        block = format_sim_block(items, harness, "qwen", is_admin=False)
        assert len(block) <= budget
        assert "[System · portable]" in block
        assert "[User · verified]" in block
        assert "verified-other-model" in block
        assert "unverified-on-current-family" in block


def test_retired_and_regressed_rules_are_never_rendered():
    items = [
        _problem(problem_id="retired", status="retired", edit_or_rule="RETIRED-RULE"),
        _problem(problem_id="bad", status="model_specific", verified_models=["claude"], regressed_models=["qwen"]),
        _problem(problem_id="good", status="verified", verified_models=["qwen"], edit_or_rule="GOOD-RULE"),
    ]
    block = format_sim_block(items, "raven", "qwen", is_admin=True)

    assert "RETIRED-RULE" not in block
    assert "Use the field name the error names." not in block
    assert "GOOD-RULE" in block


def test_admin_only_system_rules_are_hidden_unless_the_formatter_is_told_admin():
    items = [_problem(requires_admin=True, edit_or_rule="ADMIN-ONLY")]
    assert "ADMIN-ONLY" not in format_sim_block(items, "workspace", "qwen")
    assert "ADMIN-ONLY" in format_sim_block(items, "workspace", "qwen", is_admin=True)


def test_portable_wins_retrieval_priority_over_same_family_verified():
    portable = _problem(problem_id="p", status="portable", verified_models=["qwen", "claude"])
    verified = _problem(problem_id="v", status="verified", verified_models=["qwen"])
    assert status_priority(portable, "qwen") > status_priority(verified, "qwen")


def test_proposer_history_keeps_retired_and_regressed_evidence():
    summary = summarize_history(
        [_problem(status="retired", observed_outcome="regressed twice", regressed_models=["qwen"])]
    )
    assert summary[0]["status"] == "retired"
    assert summary[0]["regressed_models"] == ["qwen"]
    assert "regressed twice" in summary[0]["observed_outcome"]


def test_dataclass_round_trip_carries_two_tier_scope():
    problem = SimProblem(
        problem_id="sim-x",
        scope="user",
        user_id="alice",
        harness="workspace",
        category="habit",
        signature="sig",
        rationale="why",
        edit_or_rule="do",
    )
    assert problem.to_dict()["scope"] == "user"
