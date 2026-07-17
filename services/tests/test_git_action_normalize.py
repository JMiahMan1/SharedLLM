from services.gateway.agent_loop import _normalize_git_payload_action

_VALID = {"status","diff","add","commit","pull","push","log","fetch","reset",
          "branch","checkout","clean","show","init","remote","remote_add",
          "repo_create","repo_clone","gh_noop"}


def test_flat_tool_type_name_is_not_passed_through():
    # The exact 422 trigger: action holds the tool type name, not the verb.
    p = _normalize_git_payload_action({"action": "GitOperationRequest", "path": "."})
    assert p["action"] != "GitOperationRequest"
    assert p["action"] in _VALID


def test_nested_missing_action_with_tool_name_outer():
    p = _normalize_git_payload_action({"_outer_action": "GitOperationRequest", "path": "."})
    assert p["action"] != "GitOperationRequest"
    assert p["action"] in _VALID


def test_commit_message_infers_commit():
    p = _normalize_git_payload_action({"action": "GitOperationRequest", "commit_message": "feat: x"})
    assert p["action"] == "commit"


def test_valid_verb_untouched():
    p = _normalize_git_payload_action({"action": "push", "branch": "main"})
    assert p["action"] == "push"


def test_outer_action_carries_real_verb():
    p = _normalize_git_payload_action({"_outer_action": "add", "path": "."})
    assert p["action"] == "add"


def test_git_action_field_variant():
    p = _normalize_git_payload_action({"action": "GitOperationRequest", "git_action": "pull"})
    assert p["action"] == "pull"


def test_repo_create_inference():
    p = _normalize_git_payload_action({"action": "GitOperationRequest", "source_path": "/x", "repo_name": "r"})
    assert p["action"] == "repo_create"
