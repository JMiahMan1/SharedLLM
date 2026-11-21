"""The curriculum is derived, so adding a tool or a route adds a case.

The tests pin that each source reaches the list (household seeds first, then the
Workspace recipes, then one case per tool and per service), that filtering by
bucket is respected, and that every case carries checks the runner can score.
"""
from __future__ import annotations

from services.gateway.trainer import catalog, curriculum


def test_the_households_own_work_comes_first():
    cases = curriculum.derive_curriculum(limit=6)

    assert [case.bucket for case in cases[:6]] == ["repair", "repair", "writing",
                                                   "writing", "vision", "workspace"]
    assert cases[0].id == "seed-repair-python-bug"


def test_every_case_can_be_scored():
    for case in curriculum.derive_curriculum():
        assert case.id and case.prompt and case.title
        assert case.checks, f"{case.id} has nothing to check"
        assert case.bucket in curriculum.BUCKETS
        assert case.max_tokens > 0


def test_one_case_per_tool_in_the_registry():
    from services.gateway.tool_registry import _RAVEN_TOOL_TABLE

    cases = curriculum.tool_cases(catalog.build_catalog())
    expected = [row for row in _RAVEN_TOOL_TABLE if len(row) > 5 and row[5]]

    assert len(cases) == len(expected)
    assert {case.checks["must_include"][0] for case in cases} == {str(row[0]) for row in expected}


def test_a_tool_case_asks_for_the_tool_by_its_own_summary():
    cases = curriculum.tool_cases(catalog.build_catalog())
    bible = next(case for case in cases if case.checks["must_include"] == ["BibleRequest"])

    # The question is the tool's own summary, so the catalogue of tools is the
    # only thing that has to be maintained.
    assert "Holy Bible, read-only" in bible.prompt
    assert "Answer with the tool name only." in bible.prompt
    assert bible.bucket == "tools"
    assert bible.tags[0] == "tools"
    assert len(set(bible.tags)) == len(bible.tags), "tool tags must not repeat 'tools'"


def test_workspace_recipes_only_cover_routes_that_exist():
    cat = catalog.build_catalog()
    paths = {feature["path"] for feature in cat["features"] if feature["service"] == "workspace_runtime"}
    cases = curriculum.workspace_cases(cat)

    assert cases, "no Workspace recipe cases were derived"
    for case in cases:
        assert case.source == "catalog:workspace_runtime"
        assert case.checks["must_include"][0] in paths
        assert case.bucket == "workspace"


def test_a_route_that_does_not_exist_derives_no_case():
    """``/git/commit`` is documented but not served: the derivation must agree."""
    cat = catalog.build_catalog()
    paths = {feature["path"] for feature in cat["features"] if feature["service"] == "workspace_runtime"}

    if "/git/commit" not in paths:
        assert all("/git/commit" not in case.prompt for case in curriculum.workspace_cases(cat))


def test_bucket_filtering_and_limits_are_respected():
    only_writing = curriculum.derive_curriculum(buckets=["writing"])
    assert only_writing
    assert {case.bucket for case in only_writing} == {"writing"}

    assert len(curriculum.derive_curriculum(limit=3)) == 3
    assert curriculum.derive_curriculum(limit=0) == []


def test_case_ids_are_stable_across_builds():
    first = [case.id for case in curriculum.derive_curriculum()]
    second = [case.id for case in curriculum.derive_curriculum()]

    assert first == second


def test_summarise_counts_match_the_cases():
    cases = curriculum.derive_curriculum(buckets=["tools"])
    summary = curriculum.summarise(cases)

    assert summary["total"] == len(cases)
    assert summary["by_bucket"] == {"tools": len(cases)}
