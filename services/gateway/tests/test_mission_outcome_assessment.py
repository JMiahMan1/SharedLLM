import pytest
from services.gateway.agent_loop import should_persist_learning


class TestMissionOutcomeAssessment:
    """Tests for should_persist_learning — used by both AgentLoop RAG gate and Worker mission status."""

    def test_none_result(self):
        assert should_persist_learning(None) is False

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
