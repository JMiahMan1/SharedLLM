"""The SIM ledger: isolation, model-family lifecycle, retrieval and endpoints."""
from __future__ import annotations

import sqlite3

import pytest

from services.rag import db, sim
from services.rag import main as rag_main


def _conn(tmp_path) -> sqlite3.Connection:
    conn = db.get_db_connection(str(tmp_path / "rag.db"))
    sim.ensure_schema(conn)
    return conn


def _record(conn, **over):
    payload = dict(
        scope="system",
        user_id="default",
        harness="satellite",
        category="tool_recovery",
        signature="websearch :: validation error",
        rationale="The tool rejected an alias.",
        edit_or_rule="Use the canonical tool name from the error.",
        model="qwen3-6-35b",
    )
    payload.update(over)
    return sim.record(conn, **payload)


def test_system_text_is_scrubbed_before_storage(tmp_path):
    conn = _conn(tmp_path)
    problem = _record(
        conn,
        rationale="Alice at alice@example.com hit this in /Users/alice/private.py",
        edit_or_rule="Never print token sk-abcdef123456789.",
    )

    assert "alice@example.com" not in problem["rationale"]
    assert "/Users/alice" not in problem["rationale"]
    assert "sk-abcdef123456789" not in problem["edit_or_rule"]
    assert problem["user_id"] == "default"


def test_user_sim_is_isolated_and_system_sim_is_shared(tmp_path):
    conn = _conn(tmp_path)
    _record(conn, scope="user", user_id="alice", signature="alice habit", edit_or_rule="ALICE-ONLY")
    _record(conn, scope="system", signature="shared", edit_or_rule="SHARED-RULE")

    alice = sim.query(conn, user_id="alice", harness="workspace", query="alice shared")
    bob = sim.query(conn, user_id="bob", harness="workspace", query="alice shared")

    alice_rules = " ".join(i["edit_or_rule"] for i in alice)
    bob_rules = " ".join(i["edit_or_rule"] for i in bob)
    assert "ALICE-ONLY" in alice_rules and "SHARED-RULE" in alice_rules
    assert "ALICE-ONLY" not in bob_rules
    assert "SHARED-RULE" in bob_rules


def test_admin_only_system_lessons_are_filtered_for_non_admins(tmp_path):
    conn = _conn(tmp_path)
    _record(conn, requires_admin=True, edit_or_rule="ONLY-ADMINS")

    assert sim.query(conn, user_id="alice", harness="raven", query="admins") == []
    admin = sim.query(conn, user_id="alice", harness="raven", query="only-admins", is_admin=True)
    assert any("ONLY-ADMINS" in item["edit_or_rule"] for item in admin)


def test_improved_on_two_families_becomes_portable_and_ranks_first(tmp_path):
    conn = _conn(tmp_path)
    portable = _record(conn, signature="portable", edit_or_rule="PORTABLE")
    assert portable["status"] == "candidate"
    portable = sim.record_outcome(
        conn, problem_id=portable["problem_id"], observed_outcome="worked",
        outcome_status="improved", model="qwen3-6-35b",
    )
    assert portable["status"] == "verified"
    portable = sim.record_outcome(
        conn, problem_id=portable["problem_id"], observed_outcome="worked",
        outcome_status="improved", model="claude-sonnet-4-5",
    )
    assert portable["status"] == "portable"
    assert set(portable["verified_models"]) == {"qwen", "claude"}

    _record(conn, signature="portable", edit_or_rule="PORTABLE", model="qwen3-6-35b")
    candidate = _record(conn, signature="plain candidate", edit_or_rule="CANDIDATE")
    results = sim.query(conn, user_id="alice", harness="satellite", query="portable candidate rule")
    assert results[0]["problem_id"] == portable["problem_id"]
    assert any(item["problem_id"] == candidate["problem_id"] for item in results)


def test_model_specific_rule_is_never_injected_for_the_family_it_regressed(tmp_path):
    conn = _conn(tmp_path)
    problem = _record(conn, signature="specific", edit_or_rule="SPECIFIC-RULE", model="qwen3-6-35b")
    problem = sim.record_outcome(
        conn, problem_id=problem["problem_id"], observed_outcome="helped qwen",
        outcome_status="improved", model="qwen3-6-35b",
    )
    problem = sim.record_outcome(
        conn, problem_id=problem["problem_id"], observed_outcome="hurt claude",
        outcome_status="regressed", model="claude-sonnet-4-5",
    )

    assert problem["status"] == "model_specific"
    qwen = sim.query(conn, user_id="alice", harness="satellite", query="specific rule", model="qwen3-6-35b")
    claude = sim.query(conn, user_id="alice", harness="satellite", query="specific rule", model="claude-sonnet-4-5")
    assert any(item["problem_id"] == problem["problem_id"] for item in qwen)
    assert all(item["problem_id"] != problem["problem_id"] for item in claude)


def test_two_regressions_retire_a_rule_but_history_keeps_it(tmp_path):
    conn = _conn(tmp_path)
    problem = _record(conn, signature="bad fix", edit_or_rule="BAD-FIX", model="qwen3-6-35b")
    problem = sim.record_outcome(
        conn, problem_id=problem["problem_id"], observed_outcome="failed qwen",
        outcome_status="regressed", model="qwen3-6-35b",
    )
    assert problem["status"] == "candidate"
    problem = sim.record_outcome(
        conn, problem_id=problem["problem_id"], observed_outcome="failed claude too",
        outcome_status="regressed", model="claude-sonnet-4-5",
    )

    assert problem["status"] == "retired"
    assert sim.query(conn, user_id="alice", harness="satellite", query="bad fix") == []
    history = sim.history(conn, user_id="alice")
    assert any(item["problem_id"] == problem["problem_id"] for item in history["problems"])
    assert history["problems"][0]["attempts"]


def test_consolidate_is_idempotent_and_reports_promotions(tmp_path):
    conn = _conn(tmp_path)
    portable = _record(conn, signature="portable", edit_or_rule="PORTABLE", model="qwen")
    conn.execute(
        "UPDATE sim_problems SET verified_models = ? WHERE problem_id = ?",
        ['["qwen", "claude"]', portable["problem_id"]],
    )
    conn.commit()

    report = sim.consolidate(conn)
    assert portable["problem_id"] in report["promoted"]
    again = sim.consolidate(conn)
    assert again["updated"] == 0


def test_hybrid_query_survives_an_embedder_that_raises(tmp_path):
    conn = _conn(tmp_path)
    _record(conn, signature="lexical", edit_or_rule="LEXICAL-HIT")

    def broken_embed(texts):
        raise RuntimeError("model not loaded")

    results = sim.query(
        conn, user_id="alice", harness="satellite", query="lexical hit", embed_fn=broken_embed
    )
    assert any("LEXICAL-HIT" in item["edit_or_rule"] for item in results)


@pytest.mark.asyncio
async def test_rag_sim_endpoints_record_query_and_list(tmp_path):
    conn = _conn(tmp_path)
    rag_main.conn = conn
    rag_main.embedder = None

    created = await rag_main.sim_record(
        rag_main.SimRecordRequest(
            scope="user",
            user_id="alice",
            harness="satellite",
            category="disambiguation",
            signature="harbor the song or book",
            rationale="Alice meant the song.",
            edit_or_rule="When Alice says Harbor, prefer the song.",
            model="qwen3-6-35b",
        )
    )
    assert created["status"] == "SUCCESS"

    found = await rag_main.sim_query(
        rag_main.SimQueryRequest(
            user_id="alice", harness="satellite", query="harbor song", model="qwen3-6-35b"
        )
    )
    assert found["count"] == 1
    result = found["results"][0]
    assert result["problem_id"]

    bob = await rag_main.sim_query(
        rag_main.SimQueryRequest(
            user_id="bob", harness="satellite", query="harbor song", model="qwen3-6-35b"
        )
    )
    assert bob["count"] == 0

    outcome = await rag_main.sim_outcome(
        rag_main.SimOutcomeRequest(
            problem_id=result["problem_id"],
            observed_outcome="the song was right",
            outcome_status="improved",
            model="qwen3-6-35b",
        )
    )
    assert outcome["problem"]["status"] == "verified"

    listed = await rag_main.sim_problems(scope="user", user_id="alice", limit=200)
    assert listed["count"] == 1
    consolidated = await rag_main.sim_consolidate()
    assert consolidated["status"] == "SUCCESS"


def test_matching_verified_user_rule_beats_and_excludes_an_unrelated_portable_rule(tmp_path):
    conn = _conn(tmp_path)
    verified = _record(
        conn,
        scope="user",
        user_id="alice",
        harness="workspace",
        signature="timezone formatting",
        edit_or_rule="Use the timezone formatting helper for timestamps.",
    )
    verified = sim.record_outcome(
        conn,
        problem_id=verified["problem_id"],
        observed_outcome="worked",
        outcome_status="improved",
        model="qwen3-6-35b",
    )
    assert verified["status"] == "verified"

    portable = _record(
        conn,
        harness="workspace",
        signature="manuals",
        edit_or_rule="Always cite the manual page for shell flags.",
    )
    conn.execute(
        "UPDATE sim_problems SET status = 'portable', verified_models = ? WHERE problem_id = ?",
        ['["qwen", "claude"]', portable["problem_id"]],
    )
    conn.commit()

    results = sim.query(
        conn, user_id="alice", harness="workspace", query="timezone formatting bug", model="qwen3-6-35b"
    )
    ids = [item["problem_id"] for item in results]

    # Relevance gates portability in every harness: the exact match is first,
    # and a portable rule with zero overlap is not injected at all.
    assert ids[0] == verified["problem_id"]
    assert portable["problem_id"] not in ids


def test_satellite_query_drops_unrelated_rules_even_when_portable(tmp_path):
    conn = _conn(tmp_path)
    related = _record(
        conn,
        harness="satellite",
        signature="harbor song",
        edit_or_rule="When the user says harbor, prefer the song.",
    )
    unrelated = _record(
        conn,
        harness="satellite",
        signature="flags",
        edit_or_rule="Always cite the manual page for shell flags.",
    )
    conn.execute(
        "UPDATE sim_problems SET status = 'portable', verified_models = ? WHERE problem_id = ?",
        ['["qwen", "claude"]', unrelated["problem_id"]],
    )
    conn.commit()

    results = sim.query(
        conn, user_id="alice", harness="satellite", query="harbor song", model="qwen3-6-35b"
    )
    ids = [item["problem_id"] for item in results]

    assert related["problem_id"] in ids
    assert unrelated["problem_id"] not in ids

