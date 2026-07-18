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


def test_visibility_isprivate_true():
    p = {"action": "repo_create", "isPrivate": True}
    _normalize_git_payload_action(p)
    assert p["private"] is True


def test_visibility_isprivate_false():
    p = {"action": "repo_create", "isPrivate": False}
    _normalize_git_payload_action(p)
    assert p["private"] is False


def test_visibility_public_true_means_private_false():
    p = {"action": "repo_create", "public": True}
    _normalize_git_payload_action(p)
    assert p["private"] is False


def test_visibility_public_false_means_private_true():
    p = {"action": "repo_create", "public": False}
    _normalize_git_payload_action(p)
    assert p["private"] is True


def test_visibility_visibility_field_private():
    p = {"action": "repo_create", "visibility": "private"}
    _normalize_git_payload_action(p)
    assert p["private"] is True


def test_visibility_visibility_field_public():
    p = {"action": "repo_create", "visibility": "public"}
    _normalize_git_payload_action(p)
    assert p["private"] is False


def test_visibility_canonical_private_respected():
    p = {"action": "repo_create", "private": False}
    _normalize_git_payload_action(p)
    assert p["private"] is False


def test_visibility_not_applied_for_non_repo_action():
    p = {"action": "push", "isPrivate": True}
    _normalize_git_payload_action(p)
    assert "private" not in p


def test_repo_create_never_normalized_to_note_create():
    # User reported: "repo_create failed because it tried to use an unknown
    # action 'note_create'". Verify no path turns a repo_create payload into a
    # note action — the verb must stay a valid git op.
    for p in [
        {"action": "repo_create", "repo_name": "my-repo", "private": True},
        {"action": "GitOperationRequest", "repo_name": "my-repo", "repo_url": "https://x/y.git"},
        {"action": "gitoperationrequest", "source_path": "/src"},
    ]:
        out = _normalize_git_payload_action(p.copy())
        assert out["action"] == "repo_create", out
        assert "note" not in out["action"]


def test_status_action_is_valid_and_kept():
    # User reported "status returned Unknown action". status is a documented
    # valid verb and must pass through untouched.
    p = {"action": "status"}
    out = _normalize_git_payload_action(p.copy())
    assert out["action"] == "status"


def test_repo_create_with_isPrivate_normalizes_to_private_true():
    p = {"action": "repo_create", "repo_name": "my-repo", "isPrivate": True}
    out = _normalize_git_payload_action(p.copy())
    assert out["action"] == "repo_create"
    assert out["private"] is True
