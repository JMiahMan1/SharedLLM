"""Execution registry module.

Provides ActionDispatcher for routing actions to their handlers.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("app.logic.execution.registry")

# Registry of action handlers: maps action names to async callables
_REGISTRY: Dict[str, Callable] = {}


def register(action: str) -> Callable:
    """Decorator to register a handler for an action."""
    def decorator(func: Callable) -> Callable:
        _REGISTRY[action] = func
        log.debug(f"Registered handler for action: {action}")
        return func
    return decorator


def get_handler(action: str) -> Optional[Callable]:
    """Get the handler for an action, or None if not registered."""
    return _REGISTRY.get(action)


def list_actions() -> List[str]:
    """List all registered action names."""
    return list(_REGISTRY.keys())


class ActionDispatcher:
    """
    Dispatches actions to their registered handlers.
    """

    def __init__(self, handlers: Optional[Dict[str, Callable]] = None) -> None:
        self._handlers = handlers or dict(_REGISTRY)

    def add_handler(self, action: str, handler: Callable) -> None:
        """Register a handler for an action."""
        self._handlers[action] = handler

    async def dispatch(self, action: str, **kwargs: Any) -> Dict[str, Any]:
        """
        Dispatch an action to its handler.

        Args:
            action: The action name to dispatch.
            **kwargs: Arguments to pass to the handler.

        Returns:
            Dict with 'status' and optionally 'data' or 'detail'.
        """
        handler = self._handlers.get(action)
        if handler is None:
            return {
                "status": "FAILURE",
                "detail": f"No handler registered for action: {action}",
            }

        try:
            result = await handler(**kwargs)
            if isinstance(result, dict):
                return result
            return {"status": "SUCCESS", "data": result}
        except Exception as e:
            log.error(f"Dispatch error for action '{action}': {e}")
            return {"status": "FAILURE", "detail": str(e)}
