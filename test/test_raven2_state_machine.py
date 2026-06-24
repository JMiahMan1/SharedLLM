#!/usr/bin/env python3
"""
Test suite for Raven 2.0 State Machine integration
"""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock, patch
from services.gateway.schemas import ResolvedCredentials
from services.gateway.state_machine import StateMachine, run_state_machine_agent

class TestStateMachine:
    """Test the Raven 2.0 StateMachine implementation."""
    
    def test_state_initialization(self):
        """Test StateMachine initialization."""
        sm = StateMachine(mission_id=1)
        assert sm.mission_id == 1
        assert 'query' in sm.state
        assert 'agent_type' in sm.state
        assert sm.state['agent_type'] is None
    
    async def test_run_method_signature(self):
        """Test that run method exists and is callable."""
        sm = StateMachine(mission_id=1)
        assert hasattr(sm, 'run')
        assert callable(sm.run)
    
    async def test_agent_execution(self):
        """Test basic agent execution workflow."""
        with patch('services.gateway.state_machine.AgentLoop') as mock_agent_loop:
            mock_agent_loop.return_value = "Test response"
            
            result = await run_state_machine_agent(
                query="Test query",
                selected_model="test-model",
                full_system="Test system",
                short_term=[],
                rag_user="test_user",
                creds={"user": "test", "is_admin": False},
                mission_id=1
            )
            
            assert "mission_id" in result
            assert result["mission_id"] == 1
            assert "final_answer" in result
            assert result["status"] == "completed" or result["status"] == "failed"

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
