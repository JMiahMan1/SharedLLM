import pytest
from services.gateway.background_worker import RavenWorker


class TestModelUpgrade:
    """Tests for automatic model upgrade on schema failures."""

    def setup_method(self):
        self.worker = RavenWorker()

    def test_schema_error_triggers_upgrade(self):
        result = 'SCHEMA ERROR (422): [{"type": "missing", "loc": ["body", "patch"]}]'
        payload = {"_retry_count": 0}
        assert self.worker._should_upgrade_model(result, payload) is True

    def test_short_suspicious_success_triggers_upgrade(self):
        result = "Successfully wrote to services/tests/test_identity_resolution.py."
        payload = {"_retry_count": 0}
        assert self.worker._should_upgrade_model(result, payload) is True

    def test_no_upgrade_after_max_retries(self):
        result = 'SCHEMA ERROR (422): [{"type": "missing"}]'
        payload = {"_retry_count": 1}
        assert self.worker._should_upgrade_model(result, payload) is False

    def test_meaningful_failure_no_upgrade(self):
        result = "The CI test was fixed by adding an Authorization header. Verified with pytest."
        payload = {"_retry_count": 0}
        assert self.worker._should_upgrade_model(result, payload) is False

    def test_read_only_no_upgrade(self):
        result = "Read 11 lines from services/tests/test_identity_resolution.py (offset=0)"
        payload = {"_retry_count": 0}
        assert self.worker._should_upgrade_model(result, payload) is False

    def test_validation_error_triggers_upgrade(self):
        result = "Validation error: field 'patch' is required"
        payload = {"_retry_count": 0}
        assert self.worker._should_upgrade_model(result, payload) is True
