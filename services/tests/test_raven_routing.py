import pytest
from unittest.mock import patch, AsyncMock

from gateway.main import select_model_for_query, select_system_instruction_for_query
from gateway.prompts import CODE_HELPER_SYSTEM_INSTRUCTION, RAVEN_AUTONOMOUS_PROTOCOL


@pytest.mark.asyncio
async def test_use_raven_routes_to_raven_prompt_and_coding_model():
    with patch("gateway.main.get_coding_model", new=AsyncMock(return_value="qwen2.5-coder:7b")):
        model = await select_model_for_query("Use Raven to self repair the gateway service")
        prompt = select_system_instruction_for_query("Use Raven to self repair the gateway service", model)

        assert isinstance(model, str) and len(model) > 0
        assert prompt == RAVEN_AUTONOMOUS_PROTOCOL


def test_general_repair_without_raven_uses_raven_autonomous_prompt():
    prompt = select_system_instruction_for_query("Please fix the app and inspect the logs", "qwen2.5-coder:7b")
    assert prompt == RAVEN_AUTONOMOUS_PROTOCOL


def test_coding_query_still_uses_code_helper_prompt():
    prompt = select_system_instruction_for_query("Fix this Python traceback in the gateway service", "qwen2.5-coder:7b")

    assert prompt == CODE_HELPER_SYSTEM_INSTRUCTION
