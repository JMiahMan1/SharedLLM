
from services.gateway.agent_loop import _parse_lesson_marker


def test_parse_lesson_marker_extracts_all_fields():
    text = (
        "RULE: When the repo defaults to master, rename to main before push.\n"
        "ROOT CAUSE: GitHub creates default branch master, not main.\n"
        "OUTCOME: success\n"
        "CONFIDENCE: 0.9\n"
        "SUPERSEDES: lesson-abc123 lesson-def456\n\n"
        "The push then landed on origin/main."
    )
    out = _parse_lesson_marker(text)
    assert out["rule"] == "When the repo defaults to master, rename to main before push."
    assert out["root_cause"] == "GitHub creates default branch master, not main."
    assert out["outcome"] == "success"
    assert abs(out["confidence"] - 0.9) < 1e-9
    assert set(out["supersedes"]) == {"lesson-abc123", "lesson-def456"}


def test_parse_lesson_marker_handles_markdown_and_partial():
    # Markdown **KEY:** form + missing optional fields.
    text = (
        "**RULE:** Use repo_create to wire origin.\n"
        "**ROOT CAUSE:** manual remote_add was brittle.\n"
        "Only the rule and cause present."
    )
    out = _parse_lesson_marker(text)
    assert out["rule"] == "Use repo_create to wire origin."
    assert out["root_cause"] == "manual remote_add was brittle."
    assert "outcome" not in out
    assert "confidence" not in out
    assert "supersedes" not in out


def test_parse_lesson_marker_empty():
    assert _parse_lesson_marker("") == {}
    assert _parse_lesson_marker("no structured fields here, just prose") == {}
