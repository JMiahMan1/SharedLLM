from typing import Dict, Any, Callable, Awaitable, Optional
from app.settings import log

# Type definition for tool handlers
# Handlers receive common context arguments and specific parameters
ToolHandler = Callable[..., Awaitable[Dict[str, Any]]]

class ActionDispatcher:
    """
    Central registry for tool execution.
    Replaces the monolithic if/else block in pipeline.py.
    """
    _registry: Dict[str, ToolHandler] = {}

    @classmethod
    def register(cls, name: str):
        """Decorator to register a function as a tool handler."""
        def decorator(func: ToolHandler):
            if name in cls._registry:
                log.warning(f"Tool '{name}' is being overwritten in registry.")
            cls._registry[name] = func
            return func
        return decorator

    @classmethod
    async def dispatch(cls, tool_name: str, **kwargs) -> Dict[str, Any]:
        """
        Execute a registered tool by name.
        kwargs should contain all necessary context (query, user_creds, model, etc.)
        """
        handler = cls._registry.get(tool_name)
        if not handler:
            log.error(f"Tool '{tool_name}' not found in registry.")
            return {
                "status": "FAILURE",
                "message": f"Action requested unhandled tool: {tool_name}",
                "service": tool_name,
            }
        
        try:
            # log.debug(f"Dispatching tool: {tool_name}")
            return await handler(**kwargs)
        except Exception as e:
            log.exception(f"Error executing tool '{tool_name}': {e}")
            return {
                "status": "FAILURE",
                "message": f"Tool execution failed: {str(e)}",
                "service": tool_name
            }

    @classmethod
    def list_tools(cls):
        return list(cls._registry.keys())
