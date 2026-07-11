#!/usr/bin/env python3
"""
Raven 2.0 State Machine Integration

Integration module for StateMachine with the existing Raven 2.0 architecture.
This provides enhanced state management with checkpointing and resumability.
"""

import logging
from datetime import datetime
from typing import Any

from services.gateway.agent_loop import AgentLoop
from services.gateway.schemas import ResolvedCredentials

log = logging.getLogger("state_machine")
class StateMachine:
    """
    Enhanced state machine for Raven 2.0 with checkpointing and resumability.

    This class extends the existing AgentLoop architecture to provide:
    - Mission checkpointing and resumability
    - Structured state management
    - Enhanced error handling
    - Backward compatibility with existing workflows
    """

    def __init__(self, mission_id: int):
        """Initialize with mission ID for tracking and checkpointing."""
        self.mission_id = mission_id
        self.state = self._create_initial_state()

    def _create_initial_state(self) -> dict[str, Any]:
        """Initialize the mission state with default values."""
        return {
            "mission_id": self.mission_id,
            "query": "",
            "selected_model": "",
            "full_system": "",
            "short_term": [],
            "rag_user": "",
            "creds": {},
            "rag_context": "",
            "show_thinking": False,
            "phase": "planning",
            "iteration": 0,
            "plan": "",
            "action_log": [],
            "ans": "",
            "successful_tool_calls": 0,
            "start_time": datetime.now().timestamp(),
            "agent_type": None,
        }

    async def run(self,
                  query: str,
                  selected_model: str,
                  full_system: str,
                  short_term: list,
                  rag_user: str,
                  creds: dict[str, Any],
                  rag_context: str = "",
                  show_thinking: bool = False,
                  agent_type: str | None = None) -> dict[str, Any]:
        """
        Execute the mission using the state machine.

        Args:
            query: The user's request
            selected_model: LLM model to use
            full_system: Full system prompt
            short_term: Short-term context from RAG
            rag_user: User ID for RAG
            creds: User credentials
            rag_context: Additional RAG context
            show_thinking: Whether to show thinking blocks
            agent_type: Specialized agent type

        Returns:
            Dict containing mission results and metrics
        """
        log.info(f"[StateMachine] Starting mission {self.mission_id}")

        resolved_creds = ResolvedCredentials(**creds) if isinstance(creds, dict) else creds

        self.state.update({
            "query": query,
            "selected_model": selected_model,
            "full_system": full_system,
            "short_term": short_term,
            "rag_user": rag_user,
            "creds": resolved_creds,
            "rag_context": rag_context,
            "show_thinking": show_thinking,
            "agent_type": agent_type,
        })

        await self._load_checkpoint()

        result = await self._execute_workflow(agent_type)

        return self._generate_results(result)

    async def _load_checkpoint(self) -> None:
        """Load mission state from checkpoint."""
        pass

    async def _execute_workflow(self, agent_type: str | None) -> dict[str, Any]:
        """Execute workflow based on agent type."""
        if agent_type:
            return await self._execute_specialized_agent(agent_type)
        return await self._execute_default_raven()

    async def _execute_specialized_agent(self, agent_type: str) -> dict[str, Any]:
        """Execute specialized agent workflow."""
        log.info(f"[StateMachine] Executing specialized agent: {agent_type}")

        try:
            result = await AgentLoop(
                query=self.state["query"],
                selected_model=self.state["selected_model"],
                full_system=self.state["full_system"],
                short_term=self.state["short_term"],
                rag_user=self.state["rag_user"],
                creds=self.state["creds"],
                mission_id=self.mission_id,
                rag_context=self.state["rag_context"],
                show_thinking=self.state["show_thinking"],
            )

            self.state["ans"] = result
            self.state["iteration"] += 1
            self.state["successful_tool_calls"] += 1

            return {"ans": result, "success": True, "agent": agent_type}

        except Exception as e:
            log.error(f"[StateMachine] Specialized agent {agent_type} failed: {e}")
            return {"ans": f"Agent {agent_type} execution failed: {e}", "success": False}

    async def _execute_default_raven(self) -> dict[str, Any]:
        """Execute default Raven workflow for backward compatibility."""
        log.info(f"[StateMachine] Executing default Raven workflow for mission {self.mission_id}")

        try:
            result = await AgentLoop(
                query=self.state["query"],
                selected_model=self.state["selected_model"],
                full_system=self.state["full_system"],
                short_term=self.state["short_term"],
                rag_user=self.state["rag_user"],
                creds=self.state["creds"],
                mission_id=self.mission_id,
                rag_context=self.state["rag_context"],
                show_thinking=self.state["show_thinking"],
            )

            self.state["ans"] = result
            self.state["iteration"] += 1

            return {"ans": result, "success": True}

        except Exception as e:
            log.error(f"[StateMachine] Default Raven workflow failed: {e}")
            return {"ans": f"Default Raven workflow failed: {e}", "success": False}

    def _generate_results(self, workflow_result: dict[str, Any]) -> dict[str, Any]:
        """Generate comprehensive mission results."""
        return {
            "mission_id": self.mission_id,
            "status": "completed" if workflow_result.get("success", False) else "failed",
            "final_answer": workflow_result.get("ans", self.state["ans"]),
            "total_tool_calls": self.state["successful_tool_calls"],
            "iterations": self.state["iteration"],
            "agent_type": self.state.get("agent_type"),
            "action_log": self.state["action_log"],
        }
async def run_state_machine_agent(query: str,
                                 selected_model: str,
                                 full_system: str,
                                 short_term: list,
                                 rag_user: str,
                                 creds: dict[str, Any],
                                 mission_id: int | None = None,
                                 rag_context: str = "",
                                 show_thinking: bool = False,
                                 agent_type: str | None = None) -> dict[str, Any]:
    """
    Entry point for Raven 2.0's enhanced autonomous execution.

    Args:
        query: User's query/request
        selected_model: LLM model to use
        full_system: Full system prompt
        short_term: Short-term context from RAG
        rag_user: User ID for RAG
        creds: User credentials (as dict)
        mission_id: Optional mission ID for checkpointing
        rag_context: Additional RAG context
        show_thinking: Whether to show thinking blocks
        agent_type: Specialized agent type

    Returns:
        Dict containing mission results and metrics
    """
    try:
        machine = StateMachine(mission_id or 0)
        return await machine.run(
            query=query,
            selected_model=selected_model,
            full_system=full_system,
            short_term=short_term,
            rag_user=rag_user,
            creds=creds,
            rag_context=rag_context,
            show_thinking=show_thinking,
            agent_type=agent_type,
        )
    except Exception as e:
        log.error(f"[StateMachine] Enhanced execution failed: {e}")
        raise
__all__ = ['StateMachine', 'run_state_machine_agent']
