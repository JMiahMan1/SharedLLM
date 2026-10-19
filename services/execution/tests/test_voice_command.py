from unittest.mock import AsyncMock

from services.execution import main as execution_main


async def test_unmatched_voice_command_returns_failure(monkeypatch):
    monkeypatch.setattr(execution_main, "_check_internal_secret", AsyncMock())
    result = await execution_main.execute_voice_command(
        {"transcript": "do something mysterious", "user_id": "test"}, "secret"
    )
    assert result["status"] == "FAILURE"
    assert "Unsupported voice command" in result["message"]
