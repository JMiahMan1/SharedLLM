# app/logic/__init__.py

from .pipeline import generate_rag_stream, try_handle_compound_command
from .utils import (
    contextualize_query,
    call_ollama_generate,
    call_openai_chat,
    get_ha_context,
    get_rag_context,
    update_history
)
from .web_search import tool_web_search
from .calendar_ops import (
    tool_calendar_list,
    tool_calendar_add,
    tool_calendar_delete,
    tool_calendar_update,
    tool_calendar_read
)
from .media_ops import handle_media_command
