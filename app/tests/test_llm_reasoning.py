import pytest
from app.logic.pipeline import _llm_orchestrator
from unittest.mock import patch

@pytest.mark.asyncio
async def test_llm_tool_selection_ha_service():
    """Test that a standard HA service tool call is parsed correctly."""
    query = "Turn on the living room TV"
    
    with patch('app.logic.pipeline.call_openai_chat') as mock_llm:
        mock_llm.return_value = {
            "choices": [{"message": {
                "tool_calls": [{
                    "function": {
                        "name": "execute_ha_service",
                        "arguments": '{"domain": "media_player", "service": "turn_on", "entity_id": "media_player.living_room_tv"}'
                    }
                }]
            }}]
        }
        
        result = await _llm_orchestrator(query, intent="turn_on", score=0.99, model="gpt-4o")
        
        assert result["action"] == "tool_call"
        assert result["tool_name"] == "execute_ha_service"
        assert result["parameters"]["domain"] == "media_player"
        assert result["parameters"]["service"] == "turn_on"
        assert result["parameters"]["entity_id"] == "media_player.living_room_tv"

@pytest.mark.asyncio
async def test_llm_converse_fallback():
    """Test that standard text responses are treated as CONVERSE."""
    query = "Hello, who are you?"
    
    with patch('app.logic.pipeline.call_openai_chat') as mock_llm:
        mock_llm.return_value = {
            "choices": [{"message": {
                "content": "I am an AI assistant here to help."
            }}]
        }
        
        result = await _llm_orchestrator(query, intent="general_query", score=0.85, model="gpt-4o")
        
        assert result["action"] == "CONVERSE"
        assert "error" in result # We map text content to error for CONVERSE
        assert result["error"] == "I am an AI assistant here to help."

@pytest.mark.asyncio
async def test_llm_locked_intent_behavior():
    """Test that locked intents modify the system prompt correctly."""
    query = "pause playback"
    
    with patch('app.logic.pipeline.call_openai_chat') as mock_llm:
        mock_llm.return_value = {
            "choices": [{"message": {
                "tool_calls": [{
                    "function": {
                        "name": "execute_ha_service",
                        "arguments": '{"domain": "media_player", "service": "media_pause"}'
                    }
                }]
            }}]
        }
        
        result = await _llm_orchestrator(query, intent="pause_media", score=1.0, model="gpt-4o", intent_locked=True)
        
        assert result["action"] == "tool_call"
        assert result["tool_name"] == "execute_ha_service"
        # We also verify the mock was called with the locked intent prompt
        call_args = mock_llm.call_args[1]["messages"]
        system_prompt = call_args[0]["content"] if call_args[0]["role"] == "system" else call_args[1]["content"]
        assert "CRITICAL: The intent 'pause_media' is LOCKED." in system_prompt

@pytest.mark.asyncio
async def test_llm_handles_empty_response():
    """Test resilience against completely empty or malformed LLM responses."""
    query = "Do something unknown"
    
    with patch('app.logic.pipeline.call_openai_chat') as mock_llm:
        mock_llm.return_value = {} # Empty dict
        
        result = await _llm_orchestrator(query, intent="unknown", score=0.0, model="gpt-4o")
        
        assert result["action"] == "CONVERSE"
        assert result["error"] == "Orchestrator decided not to use a tool."

@pytest.mark.asyncio
async def test_llm_handles_json_decode_error():
    """Test resilience against bad JSON arguments from the LLM."""
    query = "Turn on lights"
    
    with patch('app.logic.pipeline.call_openai_chat') as mock_llm:
        mock_llm.return_value = {
            "choices": [{"message": {
                "tool_calls": [{
                    "function": {
                        "name": "execute_ha_service",
                        "arguments": '{bad_json: true}' # Invalid JSON
                    }
                }]
            }}]
        }
        
        result = await _llm_orchestrator(query, intent="turn_on", score=0.9, model="gpt-4o")
        
        # It should catch the JSON error and fallback to CONVERSE
        assert result["action"] == "CONVERSE"
        assert result["error"] == "Orchestrator failed to generate a valid plan."

@pytest.mark.asyncio
async def test_llm_history_inclusion():
    """Test that conversation history is properly included in the prompt."""
    query = "turn it off"
    history = "User: Turn on the office TV\nAssistant: Turned on the office TV."
    
    with patch('app.logic.pipeline.call_openai_chat') as mock_llm:
        mock_llm.return_value = {
            "choices": [{"message": {"content": "Okay."}}]
        }
        
        await _llm_orchestrator(query, intent="turn_off", score=0.9, model="gpt-4o", conversation_history=history)
        
        call_args = mock_llm.call_args[1]["messages"]
        history_prompt = call_args[0]["content"]
        assert "Conversation History:\nUser: Turn on the office TV" in history_prompt
