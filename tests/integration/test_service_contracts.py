import pytest
import os
import httpx


@pytest.mark.contract
class TestIdentityResolveContract:
    def test_resolve_request_schema(self):
        from services.identity.schemas import ResolveRequest

        req = ResolveRequest(rag_user="test_user")
        assert req.rag_user == "test_user"

        req2 = ResolveRequest(rag_user="test_user", voice_id="voice123")
        assert req2.voice_id == "voice123"

    def test_resolved_credentials_schema(self):
        from services.identity.schemas import ResolvedCredentials

        creds = ResolvedCredentials(
            user="test_user",
            is_admin=False,
            ha_url="http://ha.local:8123",
            ha_token="test-token",
        )
        assert creds.user == "test_user"
        assert creds.ha_url == "http://ha.local:8123"

    def test_resolve_request_all_fields_optional(self):
        from services.identity.schemas import ResolveRequest

        req = ResolveRequest()
        assert req.rag_user is None
        assert req.voice_id is None
        assert req.device_id is None
        assert req.api_key is None


@pytest.mark.contract
class TestExecutionRequestContracts:
    def test_light_control_request_schema(self):
        from services.execution.schemas import LightControlRequest, UserContext

        ctx = UserContext(user="test_user")
        req = LightControlRequest(
            user_context=ctx,
            entity_id="light.test_light",
            action="turn_on",
        )
        assert req.entity_id == "light.test_light"
        assert req.action == "turn_on"

    def test_media_play_request_schema(self):
        from services.execution.schemas import MediaPlayRequest, UserContext

        ctx = UserContext(user="test_user")
        req = MediaPlayRequest(
            user_context=ctx,
            entity_id="media_player.test",
            query="spotify:track:123",
            media_type="music",
        )
        assert req.media_type == "music"

    def test_tts_request_schema(self):
        from services.execution.schemas import TTSRequest, UserContext

        ctx = UserContext(user="test_user")
        req = TTSRequest(
            user_context=ctx,
            text="Hello world",
        )
        assert req.text == "Hello world"

    def test_timer_request_schema(self):
        from services.execution.schemas import TimerRequest, UserContext

        ctx = UserContext(user="test_user")
        req = TimerRequest(
            user_context=ctx,
            action="add",
            title="Test Timer",
            duration_str="5 minutes",
        )
        assert req.action == "add"
        assert req.title == "Test Timer"

    def test_calendar_request_schema(self):
        from services.execution.schemas import CalendarRequest, UserContext

        ctx = UserContext(user="test_user")
        req = CalendarRequest(
            user_context=ctx,
            action="list",
        )
        assert req.action == "list"

    def test_note_request_schema(self):
        from services.execution.schemas import NoteRequest, UserContext

        ctx = UserContext(user="test_user")
        req = NoteRequest(
            user_context=ctx,
            action="create",
            title="Test Note",
            content="Test content",
        )
        assert req.action == "create"
        assert req.title == "Test Note"

    def test_media_transport_request_schema(self):
        from services.execution.schemas import MediaTransportRequest, UserContext

        ctx = UserContext(user="test_user")
        req = MediaTransportRequest(
            user_context=ctx,
            entity_id="media_player.test",
            command="pause",
        )
        assert req.command == "pause"

    def test_user_context_schema(self):
        from services.execution.schemas import UserContext

        ctx = UserContext(user="test_user", is_admin=True)
        assert ctx.user == "test_user"
        assert ctx.is_admin is True

    def test_execution_result_schema(self):
        from services.execution.schemas import ExecutionResult

        result = ExecutionResult(
            status="SUCCESS",
            message="Light turned on",
            service="light",
        )
        assert result.status == "SUCCESS"
        assert result.service == "light"


@pytest.mark.contract
class TestStorageRequestContracts:
    def test_provider_config_schema(self):
        from services.storage.models import ProviderConfig

        config = ProviderConfig(
            kind="nextcloud",
            settings={
                "url": "http://nextcloud.local",
                "username": "admin",
                "password": "secret",
            },
        )
        assert config.kind == "nextcloud"
        assert config.settings["url"] == "http://nextcloud.local"

    def test_storage_entry_schema(self):
        from services.storage.models import StorageEntry

        entry = StorageEntry(
            path="/docs/test.txt",
            name="test.txt",
            is_dir=False,
            size=1024,
            mtime="2024-01-01T00:00:00Z",
            content_type="text/plain",
        )
        assert entry.is_dir is False
        assert entry.size == 1024

    def test_content_index_item_schema(self):
        from services.storage.models import ContentIndexItem

        item = ContentIndexItem(
            path="/docs/test.txt",
            name="test.txt",
            item_type="file",
            subtype="text",
            role="document",
            is_dir=False,
            signals=["readable"],
            extractable_capabilities=["search"],
            recommended_tools=["read"],
        )
        assert item.item_type == "file"
        assert "readable" in item.signals


@pytest.mark.contract
class TestGatewayChatContract:
    def test_chat_request_minimal(self):
        payload = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "test-model",
            "rag_user": "test_user",
        }
        assert "messages" in payload
        assert isinstance(payload["messages"], list)
        assert len(payload["messages"]) == 1
        assert payload["messages"][0]["role"] == "user"

    def test_chat_response_structure(self):
        expected_keys = {"status", "intent", "confidence"}
        response = {
            "status": "SUCCESS",
            "intent": "light",
            "confidence": 0.95,
        }
        assert expected_keys.issubset(response.keys())
        assert isinstance(response["confidence"], float)


@pytest.mark.contract
class TestLoggingContract:
    def test_log_entry_schema(self):
        from services.logging.main import LogEntry

        entry = LogEntry(
            user_id="test_user",
            service="test_service",
            level="INFO",
            message="Test message",
        )
        assert entry.user_id == "test_user"
        assert entry.level == "INFO"

    def test_log_entry_with_context(self):
        from services.logging.main import LogEntry

        entry = LogEntry(
            user_id="test_user",
            service="test_service",
            level="ERROR",
            message="Something failed",
            context={"error": "ValueError", "traceback": "stack trace"},
        )
        assert entry.context is not None
        assert entry.context["error"] == "ValueError"

    def test_log_entry_defaults(self):
        from services.logging.main import LogEntry

        entry = LogEntry(service="test_service", message="Test")
        assert entry.user_id == "system"
        assert entry.level == "INFO"
        assert entry.context is None


@pytest.mark.contract
class TestInternalSecretEnforcement:
    def test_identity_requires_internal_secret(self):
        base_url = os.getenv("IDENTITY_URL", "http://localhost:8011")
        try:
            resp = httpx.post(
                f"{base_url}/users",
                json={"username": "test"},
                timeout=5.0,
            )
            assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"
        except httpx.ConnectError:
            pytest.skip("Identity service not running")

    def test_execution_requires_internal_secret(self):
        base_url = os.getenv("EXECUTION_URL", "http://localhost:8012")
        try:
            resp = httpx.post(
                f"{base_url}/execute/light",
                json={},
                timeout=5.0,
            )
            assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"
        except httpx.ConnectError:
            pytest.skip("Execution service not running")

    def test_logging_requires_internal_secret(self):
        base_url = os.getenv("LOGGING_URL", "http://localhost:8015")
        try:
            resp = httpx.post(
                f"{base_url}/log",
                json={"service": "test", "message": "test"},
                timeout=5.0,
            )
            assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"
        except httpx.ConnectError:
            pytest.skip("Logging service not running")
