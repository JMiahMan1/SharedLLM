"""Unit tests for gh repo-create -> workspace binding helpers."""
from services.execution.handlers import gh as gh_handler


def test_extract_repo_name_basic():
    assert gh_handler._extract_repo_name(["repo", "create", "raven-3d-shooter-python"]) == "raven-3d-shooter-python"
    assert gh_handler._extract_repo_name(["repo", "create", "my-game", "--private"]) == "my-game"


def test_extract_repo_name_with_value_flags():
    args = ["repo", "create", "my-game", "--private", "-d", "A cool game", "--description", "x"]
    assert gh_handler._extract_repo_name(args) == "my-game"


def test_extract_repo_name_missing():
    assert gh_handler._extract_repo_name(["repo", "create", "--private"]) is None
    assert gh_handler._extract_repo_name(["repo", "create"]) is None


def test_extract_repo_name_source_flag_value():
    # --source takes a value that must NOT be mistaken for the name.
    args = ["repo", "create", "--source", ".", "starfall-game", "--private"]
    assert gh_handler._extract_repo_name(args) == "starfall-game"
