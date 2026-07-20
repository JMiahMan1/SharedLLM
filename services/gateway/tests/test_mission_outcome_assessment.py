from services.gateway.agent_loop import should_persist_learning


class TestMissionOutcomeAssessment:
    """Tests for should_persist_learning — used by both AgentLoop RAG gate and Worker mission status."""

    def test_none_result(self):
        assert should_persist_learning("") is False

    def test_empty_result(self):
        assert should_persist_learning("") is False

    def test_string_none(self):
        assert should_persist_learning("None") is False

    def test_tool_execution_failed_422(self):
        assert should_persist_learning('Tool execution failed (422): {"detail": "Field required"}') is False

    def test_tool_execution_failed_400(self):
        assert should_persist_learning('Tool execution failed (400): {"detail": "git status failed."}') is False

    def test_read_only_operation(self):
        assert should_persist_learning("Read 11 lines from services/tests/test_identity_resolution.py (offset=0)") is False

    def test_json_payload_without_result(self):
        import json
        payload = json.dumps({"action": "WorkspaceFilePatchRequest", "payload": {"path": "main.py"}})
        assert should_persist_learning(payload) is False

    def test_meaningful_success_message(self):
        assert should_persist_learning("The CI test was fixed by adding an Authorization header. Verified with pytest.") is True

    def test_meaningful_git_commit(self):
        assert should_persist_learning("Successfully committed changes to main branch.") is True

    def test_meaningful_file_write(self):
        assert should_persist_learning("Successfully wrote to services/tests/test_identity_resolution.py. Test now passes.") is True

    def test_traceback_indicates_failure(self):
        assert should_persist_learning("Traceback (most recent call last):\n  File \"test.py\", line 1") is False

    def test_error_prefix(self):
        assert should_persist_learning("Error: Could not connect to database") is False

    def test_failed_prefix(self):
        assert should_persist_learning("Failed: Mission could not be completed") is False

    def test_empty_short_result(self):
        assert should_persist_learning("   ") is False

    def test_single_char_result(self):
        assert should_persist_learning("x") is False

    def test_push_interrupted_by_system_timeout_is_not_complete(self):
        # REGRESSION (Bug 5 false-GREEN): mission 7 was marked 'completed' with
        # this exact result even though the GitHub repo was never created/pushed.
        # A build mission that says the push was interrupted MUST NOT be a success.
        result = (
            "The `tz` timezone CLI was successfully built with passing tests, but "
            "the final step of pushing to GitHub was interrupted by a system "
            "timeout. The core logic and verification are complete; only the "
            "repository sync remains."
        )
        assert should_persist_learning(result) is False

    def test_various_incompletion_phrasings(self):
        for r in [
            "Built the code but the push was interrupted.",
            "Everything works; only the repository sync remains.",
            "Pushing to GitHub was interrupted by the timeout.",
            "Code complete but repo creation was not completed.",
            "The commit was not pushed before the deadline.",
            "Did not finish: ran out of time during git push.",
        ]:
            assert should_persist_learning(r) is False, r

    def test_genuine_push_success_still_counts(self):
        # Guard against over-broadening: a real, fully-shipped mission must remain True.
        assert should_persist_learning(
            "Built the tz CLI, all tests pass, committed and pushed to "
            "https://github.com/JMiahMan1/raven-timezone-cli (main)."
        ) is True

    def test_standalone_status_code_422(self):
        assert should_persist_learning("422") is False

    def test_standalone_status_code_500(self):
        assert should_persist_learning("500") is False

    def test_schema_error_rejected(self):
        assert should_persist_learning("SCHEMA ERROR (422): Validation failed") is False

    def test_llm_hallucinated_failure_accepted(self):
        # The summarization prompt now prevents "failed to complete" hallucinations.
        # If the LLM does produce this, it's still a meaningful summary (not pure error).
        assert should_persist_learning("The mission failed to complete. The LLM returned empty output.") is True

    def test_git_status_meaningful(self):
        assert should_persist_learning("Git status is clean. On branch main with no changes.") is True

    def test_mixed_error_in_natural_language_accepted(self):
        # "error:" embedded in natural language should NOT be rejected
        assert should_persist_learning("The file had an error: permission denied on line 42") is True
