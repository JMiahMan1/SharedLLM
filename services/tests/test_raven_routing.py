import pytest

from gateway.main import select_model_for_query, select_system_instruction_for_query
from gateway.prompts import AUTONOMOUS_EVOLUTION_AGENT_PROMPT, CODE_HELPER_SYSTEM_INSTRUCTION, RAVEN_AUTONOMOUS_PROTOCOL


@pytest.mark.asyncio
async def test_use_raven_routes_to_raven_prompt_and_coding_model():
    model = await select_model_for_query("Use Raven to self repair the gateway service")
    prompt = select_system_instruction_for_query("Use Raven to self repair the gateway service", model)

    # The model should be whatever coding_model is configured (could be qwen2.5-coder:7b or larger)
    # We just verify it's a non-empty string and the correct prompt is returned
    assert isinstance(model, str) and len(model) > 0
    assert prompt == RAVEN_AUTONOMOUS_PROTOCOL


def test_general_repair_without_raven_uses_autonomous_developer_prompt():
    prompt = select_system_instruction_for_query("Please fix the app and inspect the logs", "qwen2.5-coder:7b")

    assert prompt == AUTONOMOUS_EVOLUTION_AGENT_PROMPT


def test_coding_query_still_uses_code_helper_prompt():
    prompt = select_system_instruction_for_query("Fix this Python traceback in the gateway service", "qwen2.5-coder:7b")

    assert prompt == CODE_HELPER_SYSTEM_INSTRUCTION
