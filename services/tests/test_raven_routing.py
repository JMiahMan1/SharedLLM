from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from dotenv import dotenv_values

from services.gateway.main import select_model_for_query, select_system_instruction_for_query
from services.gateway.prompts import PROMPT_CODE_HELPER_SYSTEM_INSTRUCTION, PROMPT_RAVEN_AUTONOMOUS_PROTOCOL

# Read .env directly for test values (runtime never reads .env)
_env = dotenv_values(Path(__file__).resolve().parent.parent.parent / ".env")
CODE_HELPER_SYSTEM_INSTRUCTION = _env.get(f"PROMPT_{PROMPT_CODE_HELPER_SYSTEM_INSTRUCTION}") or "Test Code Helper System Instruction"
RAVEN_AUTONOMOUS_PROTOCOL = _env.get(f"PROMPT_{PROMPT_RAVEN_AUTONOMOUS_PROTOCOL}") or "Test Raven Autonomous Protocol"


@pytest.mark.asyncio
async def test_use_raven_routes_to_raven_prompt_and_coding_model():
    with patch("services.gateway.main.get_coding_model", new=AsyncMock(return_value="qwen2.5-coder:7b")), \
         patch("services.gateway.main.load_prompt_sync", return_value=RAVEN_AUTONOMOUS_PROTOCOL):
        model = await select_model_for_query("Use Raven to self repair the gateway service")
        prompt = select_system_instruction_for_query("Use Raven to self repair the gateway service", model)

        assert isinstance(model, str) and len(model) > 0
        assert prompt == RAVEN_AUTONOMOUS_PROTOCOL


def test_general_repair_without_raven_uses_raven_autonomous_prompt():
    with patch("services.gateway.main.load_prompt_sync", return_value=RAVEN_AUTONOMOUS_PROTOCOL):
        prompt = select_system_instruction_for_query("Please fix the app and inspect the logs", "qwen2.5-coder:7b")
    assert prompt == RAVEN_AUTONOMOUS_PROTOCOL


def test_coding_query_still_uses_code_helper_prompt():
    with patch("services.gateway.main.load_prompt_sync", return_value=CODE_HELPER_SYSTEM_INSTRUCTION):
        prompt = select_system_instruction_for_query("Fix this Python traceback in the gateway service", "qwen2.5-coder:7b")

    assert prompt == CODE_HELPER_SYSTEM_INSTRUCTION
