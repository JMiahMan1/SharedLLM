"""SIM retrieval must not re-embed the whole ledger on every query."""
from __future__ import annotations

import json

from services.rag import sim
from services.rag.tests.test_rag_sim_memory import _conn, _record


def _embedding(texts):
    # Deterministic two-dimension vector: enough to prove what was embedded.
    return [[1.0, float(len(text))] for text in texts]


def test_schema_adds_the_cache_columns_idempotently(tmp_path):
    conn = _conn(tmp_path)
    sim.ensure_schema(conn)  # second call must not raise
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(sim_problems)").fetchall()
    }
    assert {"embedding", "family_stats"} <= columns


def test_migration_adds_the_columns_to_an_existing_ledger(tmp_path):
    conn = _conn(tmp_path)
    conn.execute("ALTER TABLE sim_problems DROP COLUMN embedding")
    conn.execute("ALTER TABLE sim_problems DROP COLUMN family_stats")
    conn.commit()

    sim.ensure_schema(conn)

    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(sim_problems)").fetchall()
    }
    assert {"embedding", "family_stats"} <= columns


def test_record_caches_the_embedding_and_hides_it_from_responses(tmp_path):
    conn = _conn(tmp_path)
    seen: list[list[str]] = []

    def embed(texts):
        seen.append(list(texts))
        return _embedding(texts)

    problem = _record(conn, embed_fn=embed)

    assert seen and seen[0] == [
        f"{problem['edit_or_rule']} {problem['rationale']}"
    ]
    assert "embedding" not in problem
    row = conn.execute(
        "SELECT embedding FROM sim_problems WHERE problem_id = ?", [problem["problem_id"]]
    ).fetchone()
    assert json.loads(row["embedding"])


def test_query_embeds_only_the_question_when_rows_are_cached(tmp_path):
    conn = _conn(tmp_path)
    _record(conn, embed_fn=_embedding)
    calls: list[list[str]] = []

    def embed(texts):
        calls.append(list(texts))
        return _embedding(texts)

    sim.query(
        conn,
        user_id="default",
        harness="satellite",
        query="canonical tool name",
        model="qwen3-6-35b",
        embed_fn=embed,
    )

    # One call, for the query only: the row's cached vector was reused.
    assert calls == [["canonical tool name"]]


def test_query_backfills_a_missing_vector_once_then_reuses_it(tmp_path):
    conn = _conn(tmp_path)
    rule = _record(conn, edit_or_rule="Prefer the canonical tool alias.")
    calls: list[list[str]] = []

    def embed(texts):
        calls.append(list(texts))
        return _embedding(texts)

    sim.query(
        conn, user_id="default", harness="satellite",
        query="canonical tool alias", model="qwen3-6-35b", embed_fn=embed,
    )
    assert len(calls) == 2  # the query, then the lazy backfill batch
    assert calls[0] == ["canonical tool alias"]
    assert calls[1] == [f"{rule['edit_or_rule']} {rule['rationale']}"]
    cached = conn.execute(
        "SELECT embedding FROM sim_problems WHERE problem_id = ?", [rule["problem_id"]]
    ).fetchone()["embedding"]
    assert json.loads(cached)

    calls.clear()
    sim.query(
        conn, user_id="default", harness="satellite",
        query="canonical tool alias", model="qwen3-6-35b", embed_fn=embed,
    )
    assert calls == [["canonical tool alias"]]


def test_zero_relevance_rules_are_excluded_for_raven_and_workspace(tmp_path):
    conn = _conn(tmp_path)
    unrelated = _record(
        conn,
        harness="raven",
        signature="shell flags",
        edit_or_rule="Always cite the manual page for shell flags.",
    )
    conn.execute(
        "UPDATE sim_problems SET status = 'portable', verified_models = ? WHERE problem_id = ?",
        ['["qwen", "claude"]', unrelated["problem_id"]],
    )
    conn.commit()

    for harness in ("raven", "workspace", "satellite"):
        results = sim.query(
            conn, user_id="default", harness=harness,
            query="timezone formatting bug", model="qwen3-6-35b",
        )
        assert unrelated["problem_id"] not in {item["problem_id"] for item in results}


def test_two_wins_survive_one_and_even_two_noisy_losses(tmp_path):
    conn = _conn(tmp_path)
    problem = _record(conn, signature="noisy family", edit_or_rule="Use the retry helper.")

    for _ in range(2):
        sim.record_outcome(
            conn, problem_id=problem["problem_id"], observed_outcome="worked",
            outcome_status="improved", model="qwen3-6-35b",
        )
    sim.record_outcome(
        conn, problem_id=problem["problem_id"], observed_outcome="one noisy failure",
        outcome_status="regressed", model="qwen3-6-35b",
    )
    after_one = sim.get_problem(conn, problem["problem_id"])
    assert "qwen" in after_one["verified_models"]
    assert "qwen" not in after_one["regressed_models"]
    assert after_one["family_stats"]["qwen"] == {"wins": 2, "losses": 1}

    # 2-2 is a tie: a proven family is not stripped by a second noisy turn.
    sim.record_outcome(
        conn, problem_id=problem["problem_id"], observed_outcome="second noisy failure",
        outcome_status="regressed", model="qwen3-6-35b",
    )
    tied = sim.get_problem(conn, problem["problem_id"])
    assert "qwen" in tied["verified_models"]

    # A losing majority (2-3) does demote it.
    sim.record_outcome(
        conn, problem_id=problem["problem_id"], observed_outcome="real regression",
        outcome_status="regressed", model="qwen3-6-35b",
    )
    demoted = sim.get_problem(conn, problem["problem_id"])
    assert "qwen" not in demoted["verified_models"]
    assert "qwen" in demoted["regressed_models"]
