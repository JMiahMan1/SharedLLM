"""Unit tests for multi-dialect tool call extraction."""
import json
import pytest

from services.gateway.agent_loop import _extract_tool_candidates, extract_action_json
from services.gateway.external_agent import _parse_text_tool_calls, _strip_text_calls


def test_gguf_text_protocol_extraction():
    raw_text = '<think>\nI should turn off the hall lamp.\n</think>\ncall:default_api:LightControlRequest{"entity_id": "light.hall_lamp", "action": "turn_off"}'
    
    # external_agent parser
    calls = _parse_text_tool_calls(raw_text)
    assert len(calls) == 1
    assert calls[0]["name"] == "LightControlRequest"
    assert calls[0]["arguments"]["entity_id"] == "light.hall_lamp"
    assert calls[0]["arguments"]["action"] == "turn_off"
    
    # agent_loop candidate extraction
    cands = _extract_tool_candidates(raw_text)
    assert any(c.get("action") == "LightControlRequest" and c.get("payload", {}).get("entity_id") == "light.hall_lamp" for c in cands)


def test_qwen_tool_code_extraction():
    raw_text = '<think>\nTurning off the hall lamp now.\n</think>\n<tool_code>\n{"tool": "LightControlRequest", "entity_id": "light.hall_lamp", "action": "turn_off"}\n</tool_code>'
    
    # external_agent parser
    calls = _parse_text_tool_calls(raw_text)
    assert len(calls) == 1
    assert calls[0]["name"] == "LightControlRequest"
    assert calls[0]["arguments"]["entity_id"] == "light.hall_lamp"
    
    # agent_loop extract_action_json
    action = extract_action_json(raw_text)
    assert action is not None
    assert action.get("action") == "LightControlRequest"
    assert action.get("entity_id") == "light.hall_lamp"


def test_fenced_json_extraction():
    raw_text = 'I will turn off the light for you.\n```json\n{"action": "LightControlRequest", "payload": {"entity_id": "light.hall_lamp", "action": "turn_off"}}\n```'
    
    # agent_loop extract_action_json
    action = extract_action_json(raw_text)
    assert action is not None
    assert action.get("action") == "LightControlRequest"
    assert action.get("entity_id") == "light.hall_lamp" or action.get("payload", {}).get("entity_id") == "light.hall_lamp"


def test_strip_text_calls_cleans_content():
    content = 'Before call:default_api:LightControlRequest{"entity_id": "light.hall_lamp"} After.'
    cleaned = _strip_text_calls(content)
    assert "call:default_api" not in cleaned
    assert "Before" in cleaned
    assert "After" in cleaned
